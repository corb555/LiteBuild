from concurrent.futures import ProcessPoolExecutor, as_completed
import os
import subprocess
import time
import traceback
from typing import Dict, Tuple

import networkx as nx

from LiteBuild.build_logger import BuildLogger, get_logger, LINE_SEPARATOR
from LiteBuild.common import BuildPlan, BuildStep
from LiteBuild.schema import YAMLSection
from LiteBuild.status_emitter import StatusEmitter, emit_status
from LiteBuild.status_message import StepFinish, StepStart, StepsSkipped


class BuildExecutor:
    """Execute a build plan, running independent DAG steps in parallel."""

    def __init__(self, state_manager, config: Dict, max_workers: int):
        self.state_manager = state_manager
        self.build_state = state_manager.load_state()
        self.config = config
        self.max_workers = max_workers

    def execute_plan(
        self,
        plan: BuildPlan,
        logger: BuildLogger,
        status_emitter: StatusEmitter | None = None,
    ) -> bool:
        """Execute one profile build plan.

        Text output is written through ``BuildLogger``. Structured step state is
        emitted independently through ``StatusEmitter``.

        Steps within the same DAG generation may execute concurrently. Their
        finish messages are therefore emitted in actual completion order rather
        than start order.
        """
        total_to_run = len(plan.steps_to_run)
        step_timings: dict[str, float] = {}
        build_start_time = time.perf_counter()

        if plan.steps_to_skip:
            skipped_names = tuple(step.node_name for step in plan.steps_to_skip)

            logger.info(f"        UP TO DATE — {len(skipped_names)} steps:")
            logger.info(f"    {', '.join(skipped_names)}")
            logger.blank()

            state_changed = False
            for step in plan.steps_to_skip:
                if not step.command.get("adopt_state"):
                    continue

                output_path = step.command["output"]
                self.build_state[step.node_name] = {
                    "output": output_path,
                    "hashes": step.command["hashes"],
                    "mtime": os.path.getmtime(output_path),
                }
                state_changed = True

            if state_changed:
                self.state_manager.save_state(self.build_state)

            emit_status(
                status_emitter,
                StepsSkipped(names=skipped_names),
            )

        if not plan.steps_to_run:
            logger.info("✅ PLAN COMPLETE — nothing to rebuild")
            return True

        logger.info(f"    {total_to_run} steps to run:")
        logger.blank()

        worker_init_info = logger.get_worker_init_info()
        initializer, initargs = (
            worker_init_info if worker_init_info else (None, ())
        )

        tasks_to_run_map = {
            step.node_name: step
            for step in plan.steps_to_run
        }
        step_indices = {
            step.node_name: index
            for index, step in enumerate(plan.steps_to_run, start=1)
        }

        for generation in nx.topological_generations(plan.execution_graph):
            steps_this_generation = [
                tasks_to_run_map[node_name]
                for node_name in generation
                if node_name in tasks_to_run_map
            ]

            if not steps_this_generation:
                continue

            with ProcessPoolExecutor(
                max_workers=self.max_workers,
                initializer=initializer,
                initargs=initargs,
            ) as executor:
                futures = {}

                for step in steps_this_generation:
                    step_index = step_indices[step.node_name]
                    status_text = step.reason_code.status_text(step.context)
                    logger.info("")
                    logger.info(
                        f"▶️  STEP {step.node_name} "
                        f"[{step_index}/{total_to_run}] - {status_text}"
                    )

                    emit_status(
                        status_emitter,
                        StepStart(
                            name=step.node_name,
                            index=step_index,
                            total=total_to_run,
                            status_text=status_text,
                            description=step.description,
                        ),
                    )
                    future = executor.submit(
                        self._run_single_command,
                        (
                            step.node_name,
                            step.command,
                        ),
                    )
                    futures[future] = step

                generation_failed = False

                for future in as_completed(futures):
                    step = futures[future]

                    try:
                        status, result_data = future.result()
                    except Exception as exc:
                        stack_text = traceback.format_exc().rstrip()

                        logger.blank()
                        logger.error(
                            f"Unexpected executor failure while collecting step "
                            f"'{step.node_name}': {type(exc).__name__}: {exc}"
                        )
                        logger.info(stack_text)

                        status = "FAILED"
                        result_data = {
                            "step_name": step.node_name,
                            "elapsed_time": 0.0,
                            "status_text": (
                                f"Unexpected executor failure: "
                                f"{type(exc).__name__}: {exc}"
                            ),
                        }

                    step_name = result_data["step_name"]
                    elapsed_s = float(result_data["elapsed_time"])
                    step_timings[step_name] = elapsed_s

                    if status == "EXECUTED":
                        logger.info(
                            f"✅ STEP {step_name} — {elapsed_s:.2f}s"
                        )

                        emit_status(
                            status_emitter,
                            StepFinish(
                                name=step_name,
                                success=True,
                                elapsed_s=elapsed_s,
                                status_text="Done",
                            ),
                        )

                        self.build_state[step_name] = {
                            "output": result_data["output_path"],
                            "hashes": result_data["hashes"],
                            "mtime": result_data["mtime"],
                        }
                        # Save the success state
                        self.state_manager.save_state(self.build_state)

                    elif status == "FAILED":
                        generation_failed = True
                        status_text = result_data["status_text"]

                        logger.blank()
                        logger.error(f"STEP {step_name} failed")
                        logger.info(f"    {status_text}")

                        emit_status(
                            status_emitter,
                            StepFinish(
                                name=step_name,
                                success=False,
                                elapsed_s=elapsed_s,
                                status_text=status_text,
                            ),
                        )

                if generation_failed:
                    self.state_manager.save_state(self.build_state)
                    return False

        self.state_manager.save_state(self.build_state)

        total_build_time = time.perf_counter() - build_start_time
        self._print_timing_report(logger, step_timings, total_build_time)

        return True

    @staticmethod
    def _run_single_command(
        task: Tuple[str, Dict],
    ) -> Tuple[str, Dict]:
        """Run one child command and stream its diagnostic output.

        Child stdout and stderr are merged into the human-readable text log.
        Structured status is intentionally not emitted from worker processes.

        CR/LF-sensitive terminal output is normalized here so progress redraws
        do not become a flood of unrelated permanent log lines.
        """
        logger = get_logger()
        step_name, command = task
        output_path = command["output"]
        cmd_string = command["cmd_string"]

        logger.info(f"    Command: {_truncate(cmd_string)}")

        start_time = time.perf_counter()

        try:
            process = subprocess.Popen(
                cmd_string,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )

            if process.stdout is None:
                raise RuntimeError("Child process stdout pipe was not created")

            _stream_tool_output(
                process.stdout,
                logger=logger,
                step_name=step_name,
            )

            return_code = process.wait()
            elapsed_s = time.perf_counter() - start_time

            if return_code != 0:
                return (
                    "FAILED",
                    {
                        "step_name": step_name,
                        "elapsed_time": elapsed_s,
                        "status_text": f"Command failed with exit code {return_code}",
                    },
                )

            if not os.path.exists(output_path):
                return (
                    "FAILED",
                    {
                        "step_name": step_name,
                        "elapsed_time": elapsed_s,
                        "status_text": f"Expected output was not created: {output_path}",
                    },
                )

            return (
                "EXECUTED",
                {
                    "step_name": step_name,
                    "output_path": output_path,
                    "hashes": command["hashes"],
                    # State mtime represents the successful completion time of
                    # this step, not necessarily the output file's mtime. Some
                    # incremental tools intentionally leave an unchanged output
                    # untouched, so its filesystem mtime may remain older than
                    # inputs that the step has already processed successfully.
                    "mtime": time.time(),
                    "elapsed_time": elapsed_s,
                    "status_text": "Done",
                },
            )

        except Exception as exc:
            elapsed_s = time.perf_counter() - start_time
            stack_text = traceback.format_exc().rstrip()

            logger.blank()
            logger.error(
                f"tool-runner failure in step '{step_name}': "
                f"{type(exc).__name__}: {exc}"
            )
            logger.info(stack_text)

            return (
                "FAILED",
                {
                    "step_name": step_name,
                    "elapsed_time": elapsed_s,
                    "status_text": (
                        f"Unexpected tool-runner failure: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                },
            )

    def _print_timing_report(
        self,
        logger: BuildLogger,
        step_timings: Dict[str, float],
        total_time: float,
    ) -> None:
        """Write a human-readable timing summary."""
        logger.blank()
        logger.info("  TIMING SUMMARY")

        sorted_steps = sorted(
            step_timings.items(),
            key=lambda item: item[1],
            reverse=True,
        )
        total_cpu_time = sum(step_timings.values())

        for step_name, duration in sorted_steps:
            percent = (duration / total_time) * 100 if total_time > 0 else 0.0
            logger.info(
                f"    {step_name:<30} "
                f"{percent:>5.1f}%   {_format_duration(duration)}"
            )

        speedup = total_cpu_time / total_time if total_time > 0 else 1.0

        logger.info(LINE_SEPARATOR)
        logger.info(
            f"    Wall time: {_format_duration(total_time)}   "
            f"Parallelism: {speedup:.1f}x"
        )


def _stream_tool_output(
    stream,
    *,
    logger: BuildLogger,
    step_name: str,
) -> None:
    """Normalize child terminal output into readable diagnostic log records.

    LF commits a normal log line. CR replaces the current transient terminal
    line. Only the latest transient value is retained until a following LF,
    another normal line, or end-of-stream. This keeps tqdm/GDAL-style progress
    useful without recording every terminal repaint.
    """
    buffer = ""
    transient = ""

    while True:
        chunk = stream.read(1)
        if chunk == "":
            break

        if chunk == "\r":
            if buffer:
                transient = buffer
                buffer = ""
            continue

        if chunk == "\n":
            text = buffer or transient
            if text:
                _log_tool_line(logger, step_name, text)
            buffer = ""
            transient = ""
            continue

        buffer += chunk

    final_text = buffer or transient
    if final_text:
        _log_tool_line(logger, step_name, final_text)


def _log_tool_line(
    logger: BuildLogger,
    step_name: str,
    text: str,
) -> None:
    """Write one child-tool line with stable subsystem identification."""
    if not text:
        return

    logger.info(
        f"    [{step_name}] {_truncate(text)}"
    )


def _format_duration(seconds: float) -> str:
    """Format an elapsed duration compactly for diagnostic text."""
    minutes = int(seconds // 60)
    remaining_seconds = seconds % 60

    if minutes:
        return f"{minutes}:{remaining_seconds:05.2f}"

    return f"{remaining_seconds:.2f}s"


def _truncate(text: str, limit: int = 600) -> str:
    """Keep the useful beginning and end of unusually long log text."""
    if len(text) <= limit:
        return text

    keep = int(limit * 0.4)
    omitted_count = len(text) - (keep * 2)

    return (
        f"{text[:keep]}"
        f"[... {omitted_count} chars truncated...]"
        f"{text[-keep:]}"
    )
