from pathlib import Path
import sys
from typing import List, Optional

import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, )


class CsvTable(QWidget):
    """
    QWidget for viewing a CSV data in a table - supports find and sort
    """
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        main_layout = QVBoxLayout(self)
        self.table_widget = QTableWidget()
        self.table_widget.setSortingEnabled(True)
        self.table_widget.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        main_layout.addWidget(self.table_widget)

        find_layout = QHBoxLayout()
        self.find_input = QLineEdit()
        self.find_input.textChanged.connect(self._reset_search)

        self.find_button = QPushButton("Find")
        self.find_button.clicked.connect(self._on_find_clicked)

        find_layout.addWidget(QLabel("Find:"))
        find_layout.addWidget(self.find_input)
        find_layout.addWidget(self.find_button)
        find_layout.addStretch()
        main_layout.addLayout(find_layout)

        self._search_results = []
        self._current_search_index = -1

    def load(self, path: Path, remove_columns: Optional[List[str]] = None):
        """Load and displays a CSV file.  Remove columns if specified """
        try:
            print(f"Loading {path}")
            df = pd.read_csv(path)
            if remove_columns:
                df = df.drop(columns=remove_columns, errors='ignore')

            self.table_widget.clear()
            self.table_widget.setRowCount(df.shape[0])
            self.table_widget.setColumnCount(df.shape[1])
            self.table_widget.setHorizontalHeaderLabels(df.columns)

            for row_idx, row_data in enumerate(df.values):
                for col_idx, value in enumerate(row_data):
                    if isinstance(value, (int, float)):
                        if value > 2147483647:   # Max C++ int
                            item = QTableWidgetItem(str(value))
                        else:
                            item = QTableWidgetItem(value)
                        item.setData(Qt.ItemDataRole.EditRole, value)
                    else:
                        txt = "" if pd.isna(value) else str(value)
                        item = QTableWidgetItem(txt)

                    self.table_widget.setItem(row_idx, col_idx, item)

            self.table_widget.resizeColumnsToContents()
            print("   Load complete")

        except Exception as e:
            print(f"err {e}")
            self.table_widget.setItem(0, 0, QTableWidgetItem(f"Error loading file"))
            raise(ValueError, "Error: File not found at '{path}'")


    def _reset_search(self):
        """
        Resets the search state. Called whenever the find text is changed.
        """
        self.table_widget.clearSelection()
        self._search_results = []
        self._current_search_index = -1

    def _on_find_clicked(self):
        """
        Handles a click on the "Find" button.
        - If it's a new search (or text has changed), it finds all occurrences.
        - If it's a subsequent search, it finds the next occurrence.
        """
        search_text = self.find_input.text()

        # If the index is -1, it means this is a new search.
        if self._current_search_index == -1:
            self._search_results = self.table_widget.findItems(
                search_text, Qt.MatchFlag.MatchContains
            )
            if not self._search_results:
                return

        # If we have results, move to the next one.
        if self._search_results:
            self._current_search_index = (self._current_search_index + 1) % len(self._search_results)
            self._highlight_current_search_result()

    def _highlight_current_search_result(self):
        """Scrolls to and selects the current search item."""
        if not self._search_results:
            return
        item = self._search_results[self._current_search_index]
        self.table_widget.setCurrentItem(item)

