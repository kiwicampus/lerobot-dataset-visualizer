"""PyQt UI for dataset curation: episode index, action buttons, save to CSV, visualizer bridge."""

from __future__ import annotations

import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIntValidator
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from dataset_curator.bridge import (
    DEFAULT_BRIDGE_PORT,
    CuratorBridge,
    run_bridge_server,
    curator_debug,
)
from dataset_curator.data import append_curation_row


class DatasetCuratorWindow(QWidget):
    ACTION_DELETE = "Delete"
    ACTION_CHANGE_PROMPT = "Change prompt"
    ACTION_KEEP = "Keep"

    DELETE_REASON_BAD = "Bad driving"
    DELETE_REASON_NON_RELEVANT = "Non relevant actions"

    def __init__(self, bridge: CuratorBridge) -> None:
        super().__init__()
        self._bridge = bridge
        self.setWindowTitle("Dataset curator")
        self._last_language_instruction = ""
        self._bridge.episode_from_viz.connect(
            self._apply_episode_from_viz,
            Qt.ConnectionType.QueuedConnection,
        )
        self._bridge.language_instruction_from_viz.connect(
            self._apply_language_instruction_from_viz,
            Qt.ConnectionType.QueuedConnection,
        )

        self._episode_edit = QLineEdit()
        self._episode_edit.setPlaceholderText("Synced from visualizer or type index")
        self._episode_edit.setValidator(QIntValidator(0, 2_147_483_647, self))
        self._episode_edit.textChanged.connect(self._on_inputs_changed)

        # Three exclusive action buttons
        self._btn_delete = QPushButton(self.ACTION_DELETE)
        self._btn_change = QPushButton(self.ACTION_CHANGE_PROMPT)
        self._btn_keep = QPushButton(self.ACTION_KEEP)
        for b in (self._btn_delete, self._btn_change, self._btn_keep):
            b.setCheckable(True)

        self._action_group = QButtonGroup(self)
        self._action_group.setExclusive(True)
        self._action_group.addButton(self._btn_delete)
        self._action_group.addButton(self._btn_change)
        self._action_group.addButton(self._btn_keep)
        self._action_group.buttonClicked.connect(self._on_action_button_clicked)

        action_row = QHBoxLayout()
        action_row.addWidget(self._btn_delete)
        action_row.addWidget(self._btn_change)
        action_row.addWidget(self._btn_keep)

        # Delete: pick one of two fixed reasons
        self._delete_box = QGroupBox("Delete reason")
        self._delete_bad = QRadioButton(self.DELETE_REASON_BAD)
        self._delete_nonrel = QRadioButton(self.DELETE_REASON_NON_RELEVANT)
        self._reason_group = QButtonGroup(self)
        self._reason_group.addButton(self._delete_bad)
        self._reason_group.addButton(self._delete_nonrel)
        del_layout = QVBoxLayout()
        del_layout.addWidget(self._delete_bad)
        del_layout.addWidget(self._delete_nonrel)
        self._delete_box.setLayout(del_layout)
        self._delete_box.hide()
        self._delete_bad.toggled.connect(self._on_inputs_changed)
        self._delete_nonrel.toggled.connect(self._on_inputs_changed)

        # Change prompt: multiline (matches visualizer language instruction)
        self._prompt_edit = QPlainTextEdit()
        self._prompt_edit.setPlaceholderText("New prompt")
        self._prompt_edit.setFixedHeight(88)
        self._prompt_edit.textChanged.connect(self._on_inputs_changed)
        self._prompt_row = QWidget()
        _pr = QVBoxLayout(self._prompt_row)
        _pr.setContentsMargins(0, 0, 0, 0)
        _pr.addWidget(QLabel("New prompt"))
        _pr.addWidget(self._prompt_edit)
        self._prompt_row.hide()

        self._save_btn = QPushButton("Save")
        self._save_btn.clicked.connect(self._on_save)
        self._save_btn.hide()

        form = QFormLayout()
        form.addRow(QLabel("Episode index"), self._episode_edit)
        form.addRow(QLabel("Action"), action_row)
        form.addRow(self._delete_box)
        form.addRow(self._prompt_row)

        root = QVBoxLayout(self)
        root.addLayout(form)

        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self._save_btn)
        root.addLayout(row)

    def _apply_episode_from_viz(self, episode_id: int) -> None:
        curator_debug(f"UI slot _apply_episode_from_viz episode_id={episode_id}")
        self._episode_edit.blockSignals(True)
        self._episode_edit.setText(str(episode_id))
        self._episode_edit.blockSignals(False)
        self._on_inputs_changed()

    def _apply_language_instruction_from_viz(self, text: str) -> None:
        curator_debug(f"UI slot _apply_language_instruction_from_viz len={len(text)}")
        self._last_language_instruction = text
        if self._btn_change.isChecked():
            self._prompt_edit.blockSignals(True)
            self._prompt_edit.setPlainText(text)
            self._prompt_edit.blockSignals(False)
            self._on_inputs_changed()

    def _on_action_button_clicked(self) -> None:
        if self._btn_delete.isChecked():
            self._delete_box.show()
            self._prompt_row.hide()
            self._prompt_edit.clear()
        elif self._btn_change.isChecked():
            self._delete_box.hide()
            self._reason_group.setExclusive(False)
            self._delete_bad.setChecked(False)
            self._delete_nonrel.setChecked(False)
            self._reason_group.setExclusive(True)
            self._prompt_row.show()
            self._prompt_edit.blockSignals(True)
            self._prompt_edit.setPlainText(self._last_language_instruction)
            self._prompt_edit.blockSignals(False)
        else:
            self._delete_box.hide()
            self._reason_group.setExclusive(False)
            self._delete_bad.setChecked(False)
            self._delete_nonrel.setChecked(False)
            self._reason_group.setExclusive(True)
            self._prompt_row.hide()
            self._prompt_edit.clear()
        self._on_inputs_changed()

    def _episode_index_value(self) -> int | None:
        text = self._episode_edit.text().strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None

    def _selected_action(self) -> str | None:
        if self._btn_delete.isChecked():
            return self.ACTION_DELETE
        if self._btn_change.isChecked():
            return self.ACTION_CHANGE_PROMPT
        if self._btn_keep.isChecked():
            return self.ACTION_KEEP
        return None

    def _detail_for_save(self) -> str | None:
        act = self._selected_action()
        if act is None:
            return None
        if act == self.ACTION_KEEP:
            return "NA"
        if act == self.ACTION_DELETE:
            if self._delete_bad.isChecked():
                return self.DELETE_REASON_BAD
            if self._delete_nonrel.isChecked():
                return self.DELETE_REASON_NON_RELEVANT
            return None
        if act == self.ACTION_CHANGE_PROMPT:
            t = self._prompt_edit.toPlainText().strip()
            return t if t else None
        return None

    def _on_inputs_changed(self) -> None:
        idx_ok = self._episode_index_value() is not None
        detail = self._detail_for_save()
        show_save = idx_ok and detail is not None
        self._save_btn.setVisible(show_save)

    def _on_save(self) -> None:
        idx = self._episode_index_value()
        action = self._selected_action()
        detail = self._detail_for_save()
        if idx is None or action is None or detail is None:
            return
        try:
            append_curation_row(idx, action, detail)
        except OSError as e:
            QMessageBox.critical(
                self,
                "Save failed",
                f"Could not write CSV:\n{e}",
            )
            return

        self._bridge.request_advance_episode()
        self._clear_form()

    def _clear_form(self) -> None:
        self._episode_edit.clear()
        for b in (self._btn_delete, self._btn_change, self._btn_keep):
            b.setChecked(False)
        self._delete_box.hide()
        self._reason_group.setExclusive(False)
        self._delete_bad.setChecked(False)
        self._delete_nonrel.setChecked(False)
        self._reason_group.setExclusive(True)
        self._prompt_edit.clear()
        self._prompt_row.hide()
        self._last_language_instruction = ""
        self._save_btn.hide()


def run_app() -> None:
    app = QApplication(sys.argv)
    port = int(os.environ.get("CURATOR_BRIDGE_PORT", str(DEFAULT_BRIDGE_PORT)))
    curator_debug(f"Starting curator; bridge port={port} CURATOR_BRIDGE_DEBUG={os.environ.get('CURATOR_BRIDGE_DEBUG', '1')}")
    bridge = CuratorBridge()
    server = run_bridge_server(bridge, port)
    w = DatasetCuratorWindow(bridge)
    w.resize(480, 340)
    w.show()
    try:
        sys.exit(app.exec())
    finally:
        server.shutdown()
