"""PyQt UI for dataset curation: episode index, action, save to CSV."""

from __future__ import annotations

import sys

from PyQt6.QtGui import QIntValidator
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from dataset_curator.data import append_curation_row


class DatasetCuratorWindow(QWidget):
    ACTION_DELETE = "Delete"
    ACTION_CHANGE_PROMPT = "Change prompt"
    ACTION_KEEP = "Keep"
    PLACEHOLDER = "— Select —"

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Dataset curator")
        self._detail: str | None = None
        self._building_ui = True

        self._episode_edit = QLineEdit()
        self._episode_edit.setPlaceholderText("e.g. 42")
        self._episode_edit.setValidator(QIntValidator(0, 2_147_483_647, self))
        self._episode_edit.textChanged.connect(self._on_inputs_changed)

        self._action_combo = QComboBox()
        self._action_combo.addItem(self.PLACEHOLDER)
        self._action_combo.addItems(
            [self.ACTION_DELETE, self.ACTION_CHANGE_PROMPT, self.ACTION_KEEP]
        )
        self._action_combo.currentIndexChanged.connect(self._on_action_changed)
        self._action_combo.currentIndexChanged.connect(self._on_inputs_changed)

        self._save_btn = QPushButton("Save")
        self._save_btn.clicked.connect(self._on_save)
        self._save_btn.hide()

        form = QFormLayout()
        form.addRow(QLabel("Episode index"), self._episode_edit)
        form.addRow(QLabel("Action"), self._action_combo)

        root = QVBoxLayout(self)
        root.addLayout(form)

        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self._save_btn)
        root.addLayout(row)

        self._building_ui = False

    def _episode_index_value(self) -> int | None:
        text = self._episode_edit.text().strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None

    def _selected_action(self) -> str | None:
        t = self._action_combo.currentText()
        if t == self.PLACEHOLDER:
            return None
        return t

    def _on_action_changed(self, _index: int) -> None:
        if self._building_ui:
            return
        action = self._selected_action()
        if action is None:
            self._detail = None
            return

        if action == self.ACTION_KEEP:
            self._detail = "NA"
            self._on_inputs_changed()
            return

        if action == self.ACTION_DELETE:
            text, ok = QInputDialog.getText(
                self, "Reason to delete", "Reason to delete:"
            )
            if ok and text.strip():
                self._detail = text.strip()
            else:
                self._reset_action_combo()
                self._detail = None
            self._on_inputs_changed()
            return

        if action == self.ACTION_CHANGE_PROMPT:
            text, ok = QInputDialog.getText(
                self, "New prompt", "New prompt:"
            )
            if ok and text.strip():
                self._detail = text.strip()
            else:
                self._reset_action_combo()
                self._detail = None
            self._on_inputs_changed()

    def _reset_action_combo(self) -> None:
        self._action_combo.blockSignals(True)
        self._action_combo.setCurrentIndex(0)
        self._action_combo.blockSignals(False)

    def _on_inputs_changed(self) -> None:
        idx_ok = self._episode_index_value() is not None
        act = self._selected_action()
        act_ok = act is not None
        detail_ok = self._detail is not None and self._detail != ""

        show_save = idx_ok and act_ok and detail_ok
        self._save_btn.setVisible(show_save)

    def _on_save(self) -> None:
        idx = self._episode_index_value()
        action = self._selected_action()
        if idx is None or action is None or not self._detail:
            return
        try:
            append_curation_row(idx, action, self._detail)
        except OSError as e:
            QMessageBox.critical(
                self,
                "Save failed",
                f"Could not write CSV:\n{e}",
            )
            return

        self._clear_form()

    def _clear_form(self) -> None:
        self._episode_edit.clear()
        self._reset_action_combo()
        self._detail = None
        self._save_btn.hide()


def run_app() -> None:
    app = QApplication(sys.argv)
    w = DatasetCuratorWindow()
    w.resize(420, 160)
    w.show()
    sys.exit(app.exec())


def main() -> None:
    run_app()


if __name__ == "__main__":
    main()
