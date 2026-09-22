"""Profile editor dialog (ADR-0016, PRD §9.1, §8.3).

The "later increment" ``catalog_window.py``'s own module docstring
forward-referenced: profile authoring (naming a profile, selecting the
catalog entries it includes, setting its attenuation) is a distinct
capability from category/word CRUD and does not belong in
``CatalogWindow``. This dialog is owned by the wizard's Profile step
(``gui/filter/wizard/profile_step.py``) instead — modal, one decision at
a time, matching the ``QInputDialog`` pattern ``CatalogWindow`` already
uses for its own single-field prompts, just scaled up to a form.

The caller passes in the same live ``CatalogService`` instance it already
holds — this dialog never loads or saves the catalog file itself except
via ``catalog_store.save_catalog`` on a successful Save, mirroring
``CatalogWindow``'s own "persist on every mutation" choice so both
screens write through the exact same function to the exact same file.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from m4bmaker.filter.catalog import CatalogService
from m4bmaker.filter.catalog_store import save_catalog
from m4bmaker.filter.models import AttenuationSettings, SchemaValidationError

_ID_ROLE = Qt.ItemDataRole.UserRole


class ProfileEditorDialog(QDialog):
    """Create or edit one :class:`~m4bmaker.filter.models.FilterProfile`.

    ``profile_id=None`` is create mode; a real id is edit mode. Either
    way, :attr:`saved_profile_id` holds the resulting profile's id once
    the dialog has been accepted — ``None`` until then.
    """

    def __init__(
        self,
        service: CatalogService,
        profile_id: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._profile_id = profile_id
        self.saved_profile_id: str | None = None

        self.setWindowTitle("Edit Profile" if profile_id else "New Profile")
        self.setMinimumSize(420, 480)

        self._build_ui()
        self._load_initial_state()

    # ── construction ─────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name"))
        self._name_input = QLineEdit()
        name_row.addWidget(self._name_input, stretch=1)
        root.addLayout(name_row)

        # Tabs (not stacked in one column) so the word list — which can
        # grow to dozens of entries across many categories — gets the
        # dialog's full height while it's the one being worked in,
        # rather than permanently sharing space with the attenuation
        # form below it, which is set once and rarely revisited.
        tabs = QTabWidget()
        tabs.addTab(self._build_words_tab(), "Words")
        tabs.addTab(self._build_attenuation_tab(), "Attenuation")
        root.addWidget(tabs, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        save_btn = QPushButton("Save")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)
        root.addLayout(btn_row)

    def _build_words_tab(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        hint_label = QLabel(
            "Check the categories and words this profile should filter for."
        )
        hint_label.setObjectName("statusLabel")
        layout.addWidget(hint_label)
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.itemChanged.connect(self._on_tree_item_changed)
        layout.addWidget(self._tree, stretch=1)
        return panel

    def _build_attenuation_tab(self) -> QWidget:
        panel = QWidget()
        form = QFormLayout(panel)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self._lead_spin = QSpinBox()
        self._lead_spin.setRange(0, 400)
        self._lead_spin.setSuffix(" ms")
        self._add_form_row(
            form,
            "Lead padding",
            self._lead_spin,
            "Extra silence added just before a flagged word starts — "
            "covers for whisper's timestamps sometimes starting a "
            "little early or late.",
        )

        self._tail_spin = QSpinBox()
        self._tail_spin.setRange(0, 500)
        self._tail_spin.setSuffix(" ms")
        self._add_form_row(
            form,
            "Tail padding",
            self._tail_spin,
            "Extra silence added just after a flagged word ends, so it "
            "doesn't get cut off if whisper's timestamp ran short.",
        )

        self._merge_spin = QSpinBox()
        self._merge_spin.setRange(0, 100)
        self._merge_spin.setSuffix(" ms")
        self._add_form_row(
            form,
            "Merge adjacency",
            self._merge_spin,
            "Flagged words closer together than this are merged into "
            "one continuous muted stretch, instead of rapidly muting "
            "and unmuting between them.",
        )

        self._fade_in_spin = QSpinBox()
        self._fade_in_spin.setRange(5, 50)
        self._fade_in_spin.setSuffix(" ms")
        self._add_form_row(
            form,
            "Fade in",
            self._fade_in_spin,
            "How long the audio takes to fade down to silent at the "
            "start of a muted stretch, instead of cutting off abruptly.",
        )

        self._fade_out_spin = QSpinBox()
        self._fade_out_spin.setRange(5, 50)
        self._fade_out_spin.setSuffix(" ms")
        self._add_form_row(
            form,
            "Fade out",
            self._fade_out_spin,
            "How long the audio takes to fade back up to normal volume "
            "at the end of a muted stretch.",
        )

        self._gain_floor_spin = QDoubleSpinBox()
        self._gain_floor_spin.setRange(-96.0, -60.0)
        self._gain_floor_spin.setDecimals(1)
        self._gain_floor_spin.setSuffix(" dBFS")
        self._add_form_row(
            form,
            "Gain floor",
            self._gain_floor_spin,
            "How quiet the audio gets during a muted stretch. Lower "
            "(more negative) is more silent — -80 dBFS is very close "
            "to complete silence.",
        )

        return panel

    @staticmethod
    def _add_form_row(
        form: QFormLayout, label_text: str, field: QWidget, description: str
    ) -> None:
        """Adds *label_text*/*field* as a normal row, then *description*
        as its own full-width row directly beneath — persistently
        visible explainer text (the User's preference over a hover
        tooltip, which a screenshot can't show and which requires
        knowing to hover over the right thing in the first place)."""
        form.addRow(label_text, field)
        description_label = QLabel(description)
        description_label.setObjectName("statusLabel")
        description_label.setWordWrap(True)
        form.addRow(description_label)

    def _load_initial_state(self) -> None:
        if self._profile_id is not None:
            profile = self._service.get_profile(self._profile_id)
            self._name_input.setText(profile.name)
            attenuation = profile.attenuation
            checked_entry_ids = set(profile.entry_ids)
        else:
            attenuation = AttenuationSettings()
            checked_entry_ids = set()

        self._lead_spin.setValue(attenuation.lead_padding_ms)
        self._tail_spin.setValue(attenuation.tail_padding_ms)
        self._merge_spin.setValue(attenuation.merge_adjacency_ms)
        self._fade_in_spin.setValue(attenuation.fade_in_ms)
        self._fade_out_spin.setValue(attenuation.fade_out_ms)
        self._gain_floor_spin.setValue(attenuation.gain_floor_db)

        self._populate_tree(checked_entry_ids)

    # ── entry tree ───────────────────────────────────────────────────────

    def _populate_tree(self, checked_entry_ids: set[str]) -> None:
        self._tree.blockSignals(True)
        self._tree.clear()

        for category in self._service.list_categories(include_archived=True):
            entries = self._service.list_entries(
                category_id=category.id, include_archived=True
            )
            # An archived category only earns a row here if the profile
            # being edited still references one of its entries — PRD
            # §9.2's "historical snapshots remain readable" extends to
            # this editor: an existing selection is never silently
            # dropped just because its category was archived since.
            visible_entries = sorted(
                (e for e in entries if not e.archived or e.id in checked_entry_ids),
                key=lambda e: e.canonical_phrase.lower(),
            )
            if not visible_entries:
                continue
            if category.archived and not any(
                e.id in checked_entry_ids for e in entries
            ):
                continue

            cat_item = QTreeWidgetItem(self._tree)
            label = category.name + (" (archived)" if category.archived else "")
            cat_item.setText(0, label)
            cat_item.setFlags(cat_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            cat_item.setData(0, _ID_ROLE, category.id)

            for entry in visible_entries:
                entry_item = QTreeWidgetItem(cat_item)
                entry_label = entry.canonical_phrase + (
                    " (archived)" if entry.archived else ""
                )
                entry_item.setText(0, entry_label)
                entry_item.setFlags(
                    entry_item.flags() | Qt.ItemFlag.ItemIsUserCheckable
                )
                entry_item.setData(0, _ID_ROLE, entry.id)
                entry_item.setCheckState(
                    0,
                    (
                        Qt.CheckState.Checked
                        if entry.id in checked_entry_ids
                        else Qt.CheckState.Unchecked
                    ),
                )
            cat_item.setExpanded(True)
            self._update_parent_checkstate(cat_item)

        self._tree.blockSignals(False)

    def _on_tree_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        parent = item.parent()
        if parent is None:
            # A category row was clicked directly — propagate to every
            # child rather than leaving them out of sync with it.
            state = item.checkState(0)
            if state != Qt.CheckState.PartiallyChecked:
                self._tree.blockSignals(True)
                for i in range(item.childCount()):
                    child = item.child(i)
                    assert child is not None
                    child.setCheckState(0, state)
                self._tree.blockSignals(False)
        else:
            self._update_parent_checkstate(parent)

    def _update_parent_checkstate(self, parent: QTreeWidgetItem) -> None:
        states = set()
        for i in range(parent.childCount()):
            child = parent.child(i)
            assert child is not None
            states.add(child.checkState(0))
        self._tree.blockSignals(True)
        if states == {Qt.CheckState.Checked}:
            parent.setCheckState(0, Qt.CheckState.Checked)
        elif states == {Qt.CheckState.Unchecked}:
            parent.setCheckState(0, Qt.CheckState.Unchecked)
        else:
            parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
        self._tree.blockSignals(False)

    def _collect_checked_entry_ids(self) -> list[str]:
        entry_ids: list[str] = []
        for i in range(self._tree.topLevelItemCount()):
            cat_item = self._tree.topLevelItem(i)
            assert cat_item is not None
            for j in range(cat_item.childCount()):
                entry_item = cat_item.child(j)
                assert entry_item is not None
                if entry_item.checkState(0) == Qt.CheckState.Checked:
                    entry_id = entry_item.data(0, _ID_ROLE)
                    entry_ids.append(entry_id)
        return entry_ids

    # ── save ─────────────────────────────────────────────────────────────

    def _on_save(self) -> None:
        name = self._name_input.text().strip()
        entry_ids = self._collect_checked_entry_ids()
        attenuation = AttenuationSettings(
            lead_padding_ms=self._lead_spin.value(),
            tail_padding_ms=self._tail_spin.value(),
            merge_adjacency_ms=self._merge_spin.value(),
            fade_in_ms=self._fade_in_spin.value(),
            fade_out_ms=self._fade_out_spin.value(),
            gain_floor_db=self._gain_floor_spin.value(),
        )
        try:
            if self._profile_id is None:
                profile = self._service.create_profile(
                    name, entry_ids=entry_ids, attenuation=attenuation
                )
            else:
                profile = self._service.update_profile(
                    self._profile_id,
                    name=name,
                    entry_ids=entry_ids,
                    attenuation=attenuation,
                )
        except SchemaValidationError as exc:
            QMessageBox.warning(self, "Invalid Profile", str(exc))
            return

        save_catalog(self._service)
        self.saved_profile_id = profile.id
        self.accept()
