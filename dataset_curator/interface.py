"""PyQt UI for dataset curation: episode index, action buttons, save to CSV, visualizer bridge."""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request

from PyQt6.QtCore import Qt, QTimer
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
from dataset_curator.data import append_curation_row, get_curation_row, get_resume_episode, update_curation_row


class DatasetCuratorWindow(QWidget):
    ACTION_DELETE = "Delete"
    ACTION_CHANGE_PROMPT = "Change prompt"
    ACTION_KEEP = "Keep"

    DELETE_REASON_BAD = "Bad driving"
    DELETE_REASON_NON_RELEVANT = "Non relevant actions"

    # Style applied to the button that matches a previously saved entry
    _PREV_ENTRY_STYLE = (
        "background-color: #7a2f2f; color: #ffcccc; border: 1px solid #cc5555;"
    )

    _SPINNER_CHARS = ["◐", "◓", "◑", "◒"]

    def __init__(self, bridge: CuratorBridge) -> None:
        super().__init__()
        self._bridge = bridge
        self.setWindowTitle("Dataset curator")
        self._last_language_instruction = ""
        self._existing_entry: tuple[str, str] | None = None
        self._is_navigating = False
        self._nav_spinner_idx = 0
        self._nav_message = ""
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
        self._episode_edit.editingFinished.connect(self._on_episode_editing_finished)

        # Three exclusive action buttons
        self._btn_delete = QPushButton(self.ACTION_DELETE)
        self._btn_change = QPushButton(self.ACTION_CHANGE_PROMPT)
        self._btn_keep = QPushButton(self.ACTION_KEEP)
        for b in (self._btn_delete, self._btn_change, self._btn_keep):
            b.setCheckable(True)
            b.toggled.connect(self._on_inputs_changed)

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
        self._save_btn.setEnabled(False)

        self._reload_viz_btn = QPushButton("Sync from visualizer")
        self._reload_viz_btn.setToolTip(
            "Ask the browser to send the current episode and instruction again "
            "(use after reopening the curator).",
        )
        self._reload_viz_btn.clicked.connect(self._on_sync_from_visualizer)

        self._goto_last_btn = QPushButton("Go to last saved")
        self._goto_last_btn.setToolTip(
            "Navigate the visualizer to the next uncurated episode."
        )
        self._goto_last_btn.clicked.connect(self._on_goto_last_saved)

        sync_row = QHBoxLayout()
        sync_row.addWidget(self._reload_viz_btn)
        sync_row.addWidget(self._goto_last_btn)

        self._nav_label = QLabel("")
        self._nav_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._nav_label.setStyleSheet(
            "color: #88aaff; font-style: italic; padding: 4px;"
        )
        self._nav_label.hide()

        self._nav_timer = QTimer(self)
        self._nav_timer.setInterval(120)
        self._nav_timer.timeout.connect(self._tick_nav_spinner)

        form = QFormLayout()
        form.addRow(QLabel("Episode index"), self._episode_edit)
        form.addRow(sync_row)
        form.addRow(QLabel("Action"), action_row)
        form.addRow(self._delete_box)
        form.addRow(self._prompt_row)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(self._nav_label)

        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self._save_btn)
        root.addLayout(row)

    def _apply_episode_from_viz(self, episode_id: int) -> None:
        curator_debug(f"UI slot _apply_episode_from_viz episode_id={episode_id}")
        self._set_navigating(False)
        self._episode_edit.blockSignals(True)
        self._episode_edit.setText(str(episode_id))
        self._episode_edit.blockSignals(False)
        self._load_episode_curation(episode_id)

    def _apply_language_instruction_from_viz(self, text: str) -> None:
        curator_debug(f"UI slot _apply_language_instruction_from_viz len={len(text)}")
        self._last_language_instruction = text
        if self._btn_change.isChecked():
            self._prompt_edit.blockSignals(True)
            self._prompt_edit.setPlainText(text)
            self._prompt_edit.blockSignals(False)
            self._on_inputs_changed()

    def _on_action_button_clicked(self) -> None:
        # Clear previous-entry highlight — user is making a fresh choice
        for b in (self._btn_delete, self._btn_change, self._btn_keep):
            b.setStyleSheet("")
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
        if self._is_navigating:
            return
        idx_ok = self._episode_index_value() is not None
        action_chosen = self._selected_action() is not None
        detail = self._detail_for_save()
        # Save only when episode is set, user picked Keep/Delete/Change, and detail is complete.
        can_save = idx_ok and action_chosen and detail is not None
        self._save_btn.setEnabled(can_save)

    def _on_episode_editing_finished(self) -> None:
        idx = self._episode_index_value()
        if idx is not None:
            self._load_episode_curation(idx)

    def _load_episode_curation(self, episode_index: int) -> None:
        """Populate the form with an existing log entry (soft-red highlight) or clear it."""
        existing = get_curation_row(episode_index)
        self._existing_entry = existing

        for b in (self._btn_delete, self._btn_change, self._btn_keep):
            b.setStyleSheet("")

        if existing is None:
            self._action_group.setExclusive(False)
            for b in (self._btn_delete, self._btn_change, self._btn_keep):
                b.setChecked(False)
            self._action_group.setExclusive(True)
            self._delete_box.hide()
            self._reason_group.setExclusive(False)
            self._delete_bad.setChecked(False)
            self._delete_nonrel.setChecked(False)
            self._reason_group.setExclusive(True)
            self._prompt_row.hide()
            self._prompt_edit.clear()
            self._on_inputs_changed()
            return

        action, detail = existing

        if action == self.ACTION_DELETE:
            self._btn_delete.blockSignals(True)
            self._btn_delete.setChecked(True)
            self._btn_delete.blockSignals(False)
            self._btn_delete.setStyleSheet(self._PREV_ENTRY_STYLE)
            self._delete_box.show()
            self._prompt_row.hide()
            self._prompt_edit.clear()
            if detail == self.DELETE_REASON_BAD:
                self._delete_bad.setChecked(True)
            elif detail == self.DELETE_REASON_NON_RELEVANT:
                self._delete_nonrel.setChecked(True)
        elif action == self.ACTION_CHANGE_PROMPT:
            self._btn_change.blockSignals(True)
            self._btn_change.setChecked(True)
            self._btn_change.blockSignals(False)
            self._btn_change.setStyleSheet(self._PREV_ENTRY_STYLE)
            self._delete_box.hide()
            self._reason_group.setExclusive(False)
            self._delete_bad.setChecked(False)
            self._delete_nonrel.setChecked(False)
            self._reason_group.setExclusive(True)
            self._prompt_row.show()
            self._prompt_edit.blockSignals(True)
            self._prompt_edit.setPlainText(detail)
            self._prompt_edit.blockSignals(False)
        else:  # Keep
            self._btn_keep.blockSignals(True)
            self._btn_keep.setChecked(True)
            self._btn_keep.blockSignals(False)
            self._btn_keep.setStyleSheet(self._PREV_ENTRY_STYLE)
            self._delete_box.hide()
            self._reason_group.setExclusive(False)
            self._delete_bad.setChecked(False)
            self._delete_nonrel.setChecked(False)
            self._reason_group.setExclusive(True)
            self._prompt_row.hide()
            self._prompt_edit.clear()

        self._on_inputs_changed()

    def _on_save(self) -> None:
        idx = self._episode_index_value()
        action = self._selected_action()
        detail = self._detail_for_save()
        if idx is None or action is None or detail is None:
            return

        if self._existing_entry is not None:
            old_action, old_detail = self._existing_entry
            reply = QMessageBox.question(
                self,
                "Update existing entry",
                f"Episode {idx} already has an entry:\n"
                f"  Action: {old_action}\n"
                f"  Detail: {old_detail}\n\n"
                f"Replace with:\n"
                f"  Action: {action}\n"
                f"  Detail: {detail}\n\n"
                "Update?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        try:
            if self._existing_entry is not None:
                update_curation_row(idx, action, detail)
            else:
                append_curation_row(idx, action, detail)
        except OSError as e:
            QMessageBox.critical(self, "Save failed", f"Could not write CSV:\n{e}")
            return

        self._bridge.request_advance_episode()
        self._clear_form()
        self._set_navigating(True, "Navigating to next episode...")

    def _clear_form(self) -> None:
        self._existing_entry = None
        self._episode_edit.clear()
        for b in (self._btn_delete, self._btn_change, self._btn_keep):
            b.setStyleSheet("")
            b.setChecked(False)
        self._delete_box.hide()
        self._reason_group.setExclusive(False)
        self._delete_bad.setChecked(False)
        self._delete_nonrel.setChecked(False)
        self._reason_group.setExclusive(True)
        self._prompt_edit.clear()
        self._prompt_row.hide()
        self._last_language_instruction = ""
        self._save_btn.setEnabled(False)

    def _tick_nav_spinner(self) -> None:
        self._nav_spinner_idx = (self._nav_spinner_idx + 1) % len(self._SPINNER_CHARS)
        self._nav_label.setText(
            f"{self._SPINNER_CHARS[self._nav_spinner_idx]}  {self._nav_message}"
        )

    def _set_navigating(self, navigating: bool, message: str = "") -> None:
        self._is_navigating = navigating
        if navigating:
            self._nav_message = message
            self._nav_label.setText(f"{self._SPINNER_CHARS[0]}  {message}")
            self._nav_label.show()
            self._nav_timer.start()
        else:
            self._nav_timer.stop()
            self._nav_label.hide()
            self._nav_label.setText("")
        # Disable all interactive widgets while navigating; keep Sync always available
        for w in (
            self._episode_edit,
            self._btn_delete, self._btn_change, self._btn_keep,
            self._save_btn, self._goto_last_btn,
        ):
            w.setEnabled(not navigating)
        if not navigating:
            self._on_inputs_changed()

    def _on_goto_last_saved(self) -> None:
        episode = get_resume_episode()
        if episode is None:
            QMessageBox.information(
                self,
                "All curated",
                "All episodes in the range have already been curated.",
            )
            return
        curator_debug(f"Go to last saved → navigateTo={episode}")
        self._bridge.request_navigate_to(episode)
        self._set_navigating(True, f"Navigating to episode {episode}...")

    def _on_sync_from_visualizer(self) -> None:
        port = int(os.environ.get("CURATOR_BRIDGE_PORT", str(DEFAULT_BRIDGE_PORT)))
        url = f"http://127.0.0.1:{port}/request-resync"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status not in (200, 204):
                    raise OSError(f"HTTP {resp.status}")
        except (OSError, urllib.error.URLError) as e:
            QMessageBox.warning(
                self,
                "Sync from visualizer failed",
                f"The browser should poll the bridge within a few hundred ms.\n"
                f"If this persists, ensure the visualizer tab is open and the bridge URL matches.\n\n{e!s}",
            )
            return
        curator_debug("UI requested resync from visualizer (GET /request-resync ok)")


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
