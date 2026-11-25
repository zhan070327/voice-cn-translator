"""Local glossary and feedback dialogs for the existing history and settings tabs."""
from __future__ import annotations
from valorant_translator.ui.texts import tr

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout)

from valorant_translator.feedback import ERROR_TYPES
from valorant_translator.translation.glossary import GAMES, game_glossary, read_default_glossary
from valorant_translator.translation.rules import Rule, now, write_document
from valorant_translator.ui.dialogs import (GlassFileDialog as QFileDialog, GlassMessageBox as QMessageBox,
                                           apply_dialog_theme, localize_buttons)
from valorant_translator.ui.glass import GlassButton

JSON_FILTER = tr("glossary_tools.0001")


def dialog(parent, title):
    window = QDialog(parent)
    window.setWindowTitle(title)
    apply_dialog_theme(window)
    return window, QVBoxLayout(window)


def preview(parent, title, text, *, confirm=False):
    window, layout = dialog(parent, title)
    if confirm:
        layout.addWidget(QLabel(tr("glossary_tools.0002")))
    contents = QPlainTextEdit(text)
    contents.setReadOnly(True)
    layout.addWidget(contents)
    controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
                                if confirm else QDialogButtonBox.StandardButton.Close)
    controls.accepted.connect(window.accept)
    controls.rejected.connect(window.reject)
    layout.addWidget(controls)
    localize_buttons(window)
    window.resize(720, 420)
    # Keep the dialog alive for the duration of exec; callers do not retain it.
    try:
        return window.exec() == QDialog.DialogCode.Accepted
    finally:
        window.deleteLater()


def entry_text(entry):
    lines = [
        (tr("glossary_tools.0003"), entry.created_at), (tr("glossary_tools.0004"), GAMES.get(entry.game, entry.game)),
        (tr("glossary_tools.0005"), f"{entry.language or tr('glossary_tools.0006')} → {entry.target_language or tr('glossary_tools.0007')}"),
        (tr("glossary_tools.0008"), entry.original), (tr("glossary_tools.0009"), entry.corrected_original),
        (tr("glossary_tools.0010"), entry.machine_translation), (tr("glossary_tools.0011"), entry.chinese),
        (tr("glossary_tools.0012"), entry.corrected_chinese), (tr("glossary_tools.0013"), entry.glossary_rules),
    ]
    return "\n".join(f"{key}：{value or tr('glossary_tools.0014')}" for key, value in lines)


def _table(layout, headers):
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.horizontalHeader().setStretchLastSection(True)
    layout.addWidget(table)
    return table


def _populate(table, rows):
    table.clearSelection()
    table.setCurrentCell(-1, -1)
    table.setRowCount(len(rows))
    for r, values in enumerate(rows):
        for c, value in enumerate(values):
            table.setItem(r, c, QTableWidgetItem(str(value)))
    table.resizeColumnsToContents()
    for col in range(table.columnCount()):
        table.setColumnWidth(col, min(table.columnWidth(col), 225))


def _buttons(layout, items):
    row = QHBoxLayout()
    buttons = {}
    for title, action in items:
        button = GlassButton(title)
        button.clicked.connect(lambda checked=False, callback=action: callback())
        row.addWidget(button)
        buttons[title] = button
    row.addStretch()
    layout.addLayout(row)
    return buttons


def _rule_editor(parent, rule):
    window, layout = dialog(parent, tr("glossary_tools.0015"))
    form = QFormLayout()
    game = QComboBox()
    for code, name in GAMES.items():
        game.addItem(name, code)
    if game.findData(rule.game) < 0:
        game.addItem(rule.game, rule.game)
    game.setCurrentIndex(game.findData(rule.game))
    source, target = QLineEdit(rule.source), QLineEdit(rule.target)
    source.setPlaceholderText(tr("glossary_tools.0016"))
    target.setPlaceholderText(tr("glossary_tools.0017"))
    original, translated = QLineEdit(rule.original), QLineEdit(rule.translated)
    corrected = QLineEdit(rule.corrected)
    priority = QSpinBox()
    priority.setRange(-1000, 1000)
    priority.setValue(rule.priority)
    fields = [(tr("glossary_tools.0018"), game), (tr("glossary_tools.0019"), source), (tr("glossary_tools.0020"), target), (tr("glossary_tools.0021"), original),
              (tr("glossary_tools.0022"), translated), (tr("glossary_tools.0023"), priority)]
    if rule.kind == "asr_correction":
        fields.insert(4, (tr("glossary_tools.0024"), corrected))
    for title, widget in fields:
        form.addRow(title, widget)
    layout.addLayout(form)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
    selected = None

    def accept():
        nonlocal selected
        changed = replace(rule, game=game.currentData(), source=source.text().strip().lower(),
                          target=target.text().strip().lower(), original=original.text().strip(),
                          translated=translated.text().strip(), corrected=corrected.text().strip(),
                          priority=priority.value(), origin=tr("glossary_tools.0025"), verified=False, updated=now())
        try:
            selected = Rule.parse(vars(changed))
        except ValueError as exc:
            QMessageBox.warning(window, tr("glossary_tools.0026"), str(exc))
            return
        window.accept()

    buttons.accepted.connect(accept)
    buttons.rejected.connect(window.reject)
    layout.addWidget(buttons)
    localize_buttons(window)
    window.exec()
    window.deleteLater()
    return selected


def default_glossary(parent):
    game = parent.settings.game
    try:
        data = read_default_glossary(game)
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
        preview(parent, tr("glossary_tools.0027"), tr("glossary_tools.0028", p0=exc))
        return
    rows = [tr("glossary_tools.0029") + GAMES.get(game, game)]
    for key, title in (
        ("exact_phrases", tr("glossary_tools.0030")), ("target_replacements", tr("glossary_tools.0031")),
        ("location_aliases", tr("glossary_tools.0032")), ("entity_aliases", tr("glossary_tools.0033")),
        ("translated_name_aliases", tr("glossary_tools.0034")), ("contextual_rewrites", tr("glossary_tools.0035")),
        ("rules", tr("glossary_tools.0036")), ("suppressed_lines", tr("glossary_tools.0037")),
        ("recognition_hotwords", tr("glossary_tools.0038")), ("uncertain_recognition_fragments", tr("glossary_tools.0039")),
        ("weapon_names", tr("glossary_tools.0040")), ("agent_names", tr("glossary_tools.0041"))
    ):
        items = data.get(key)
        if not items:
            continue
        rows.extend(("", tr("glossary_tools.0042", p0=title, p1=len(items))))
        if isinstance(items, dict):
            rows.extend(f"{source} → {', '.join(value) if isinstance(value, list) else value}"
                        for source, value in items.items())
        elif key == "rules":
            for item in items:
                if not isinstance(item, dict):
                    continue
                target = item.get("corrected") or item.get("translated") or ""
                rows.append(f"{item.get('source', '')} → {item.get('target', '')}  "
                            f"{item.get('original', '')} → {target}  [{item.get('origin', '')}]")
        elif key == "contextual_rewrites":
            rows.extend(f"{item['source']} · {', '.join(item['literal'])} → {item['natural']}"
                        for item in items)
        elif key == "suppressed_lines":
            rows.extend(f"{item.get('character', '')} · {item.get('original', '')}"
                        for item in items if isinstance(item, dict))
        else:
            rows.extend(items)
    if len(rows) == 1:
        rows.append(tr("glossary_tools.game_glossary_pending"))
    preview(parent, tr("glossary_tools.0027"), "\n".join(rows))


def manage_rules(parent, store):
    window, layout = dialog(parent, tr("glossary_tools.0045"))
    layout.addWidget(QLabel(tr("glossary_tools.0046")))
    status = QLabel()
    status.setWordWrap(True)
    layout.addWidget(status)
    table = _table(layout, (tr("glossary_tools.0047"), tr("glossary_tools.0048"), tr("glossary_tools.0049"), tr("glossary_tools.0050"), tr("glossary_tools.0051"), tr("glossary_tools.0052")))

    def refresh(message=""):
        _populate(table, [(GAMES.get(rule.game, rule.game), f"{rule.source} → {rule.target}", rule.original,
                           rule.corrected or rule.translated, rule.priority, tr("glossary_tools.0053") if rule.enabled else tr("glossary_tools.0054"))
                          for rule in store.rules])
        empty = tr("glossary_tools.0055") if not store.rules else ""
        status.setText(" ".join(filter(None, (message, empty))))
        status.setVisible(bool(message or empty))
        update_actions()

    def select():
        row = table.currentRow()
        return row if 0 <= row < len(store.rules) else None

    def edit(index=None):
        old = store.rules[index] if index is not None else Rule(parent.settings.game, "en", "zh", "")
        new = _rule_editor(window, old)
        if new is None:
            return
        rules = list(store.rules)
        if index is not None:
            rules.pop(index)
        conflict = any(rule.key == new.key for rule in rules)
        try:
            rules.append(new)
            store.save(rules)
            refresh(tr("glossary_tools.0056") if conflict else tr("glossary_tools.0057"))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(window, tr("glossary_tools.0058"), str(exc))

    def change(*, remove=False):
        index = select()
        if index is None:
            return
        rules = list(store.rules)
        if remove:
            if QMessageBox.question(window, tr("glossary_tools.0059"), tr("glossary_tools.0060")) != QMessageBox.StandardButton.Yes:
                return
            rules.pop(index)
        else:
            rules[index] = replace(rules[index], enabled=not rules[index].enabled, updated=now())
        try:
            store.save(rules)
            refresh(tr("glossary_tools.0061"))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(window, tr("glossary_tools.0062"), str(exc))

    def transfer(importing):
        picker = QFileDialog.getOpenFileName if importing else QFileDialog.getSaveFileName
        name, _ = picker(window, tr("glossary_tools.0063"), "" if importing else tr("glossary_tools.0064"), JSON_FILTER)
        if not name:
            return
        try:
            if importing:
                count = store.import_file(Path(name))
                refresh(tr("glossary_tools.0065", p0=count) if count else tr("glossary_tools.0066"))
            else:
                store.export(Path(name))
                refresh(tr("glossary_tools.0067"))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(window, tr("glossary_tools.0068"), str(exc))

    actions = _buttons(layout, [(tr("glossary_tools.0069"), edit), (tr("glossary_tools.0070"), lambda: edit(select())), (tr("glossary_tools.0071"), change),
                                (tr("glossary_tools.0072"), lambda: change(remove=True)), (tr("glossary_tools.0073"), lambda: transfer(True)),
                                (tr("glossary_tools.0074"), lambda: transfer(False))])

    def update_actions():
        for action in (tr("glossary_tools.0075"), tr("glossary_tools.0076"), tr("glossary_tools.0077")):
            actions[action].setEnabled(select() is not None)

    table.itemSelectionChanged.connect(update_actions)
    refresh()
    window.resize(850, 420)
    window.exec()
    window.deleteLater()


def submit_feedback(parent, store, entry):
    window, layout = dialog(parent, tr("glossary_tools.0078"))
    form = QFormLayout()
    source = QLabel(entry.original)
    source.setTextFormat(Qt.TextFormat.PlainText)
    source.setWordWrap(True)
    form.addRow(tr("glossary_tools.0079"), source)
    current = QLabel(entry.corrected_chinese or entry.chinese or tr("glossary_tools.0080"))
    current.setTextFormat(Qt.TextFormat.PlainText)
    current.setWordWrap(True)
    form.addRow(tr("glossary_tools.0081"), current)
    suggestion = QLineEdit()
    category = QComboBox()
    category.addItems(ERROR_TYPES)
    category.setCurrentText(tr("glossary_tools.0082"))
    note = QPlainTextEdit()
    note.setPlaceholderText(tr("glossary_tools.0083"))
    form.addRow(tr("glossary_tools.0084"), suggestion)
    form.addRow(tr("glossary_tools.0085"), category)
    form.addRow(tr("glossary_tools.0086"), note)
    layout.addLayout(form)
    controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)

    def save():
        try:
            added = store.add(entry, suggestion.text(), category.currentText(), note.toPlainText())
            parent.statusBar().showMessage(tr("glossary_tools.0087") if added else tr("glossary_tools.0088"))
            window.accept()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(window, tr("glossary_tools.0089"), str(exc))

    controls.accepted.connect(save)
    controls.rejected.connect(window.reject)
    layout.addWidget(controls)
    localize_buttons(window)
    window.exec()
    window.deleteLater()


def review_feedback(parent, store, custom_rules):
    import json

    window, layout = dialog(parent, tr("glossary_tools.0090"))
    hint = QLabel(tr("glossary_tools.0091"))
    hint.setWordWrap(True)
    layout.addWidget(hint)
    empty = QLabel()
    empty.setWordWrap(True)
    layout.addWidget(empty)
    category = QComboBox()
    category.addItems((tr("glossary_tools.0092"), *ERROR_TYPES))
    layout.addWidget(category)
    table = _table(layout, (tr("glossary_tools.0093"), tr("glossary_tools.0094"), tr("glossary_tools.0095"), tr("glossary_tools.0096"), tr("glossary_tools.0097")))
    keys = []

    def refresh():
        records = [item for item in store.entries.values()
                   if category.currentIndex() == 0 or item["category"] == category.currentText()]
        keys[:] = [item["id"] for item in records]
        _populate(table, [(item["status"], f"{item['game']} {item['source']}→{item['target']}",
                           item["original"], item["suggested"], item["category"]) for item in records])
        if store.unreadable:
            empty.setText(tr("glossary_tools.0098"))
        elif not store.entries:
            empty.setText(tr("glossary_tools.0099"))
        else:
            empty.setText(tr("glossary_tools.0100"))
        empty.setVisible(store.unreadable or not records)
        update_actions()

    def selected():
        row = table.currentRow()
        return store.entries[keys[row]] if 0 <= row < len(keys) else None

    def show():
        record = selected()
        if record:
            preview(window, tr("glossary_tools.0101"), json.dumps(record, ensure_ascii=False, indent=2))

    def mark(status):
        record = selected()
        if not record:
            return
        try:
            store.review(record["id"], status)
            if status == "Approved":
                scope = (record["game"], record["source"], record["target"], "term")
                source = record["corrected_original"] or record["original"]
                probe = Rule(*scope[:3], source, kind=scope[3])
                glossary = game_glossary(record["game"])
                built_in = (record["source"], record["target"]) == ("en", "zh") and glossary.exact(source)
                conflict = built_in or probe.key in custom_rules.index
                hint.setText(tr("glossary_tools.0102") if conflict else tr("glossary_tools.0103"))
            else:
                hint.setText(tr("glossary_tools.0104"))
            refresh()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(window, tr("glossary_tools.0105"), str(exc))

    def load():
        name, _ = QFileDialog.getOpenFileName(window, tr("glossary_tools.0106"), "", JSON_FILTER)
        if name:
            try:
                imported, duplicate = store.import_file(Path(name))
                hint.setText(tr("glossary_tools.0107", p0=imported, p1=duplicate))
                refresh()
            except (OSError, ValueError) as exc:
                QMessageBox.warning(window, tr("glossary_tools.0108"), str(exc))

    def export(candidates=False):
        if candidates:
            data = {"version": 1, "rules": [vars(rule) for rule in store.approved_candidates()]}
        else:
            data = {"version": 1, "feedback": list(store.entries.values())}
        if not preview(window, tr("glossary_tools.0109"), json.dumps(data, ensure_ascii=False, indent=2), confirm=True):
            return
        name, _ = QFileDialog.getSaveFileName(window, tr("glossary_tools.0110"),
                                             tr("glossary_tools.0111") if candidates else tr("glossary_tools.0112"), JSON_FILTER)
        if name:
            try:
                write_document(Path(name), data)
                hint.setText(tr("glossary_tools.0113"))
            except (OSError, ValueError) as exc:
                QMessageBox.warning(window, tr("glossary_tools.0114"), str(exc))

    category.currentIndexChanged.connect(refresh)
    table.doubleClicked.connect(show)
    actions = _buttons(layout, [(tr("glossary_tools.0115"), load), (tr("glossary_tools.0116"), show), (tr("glossary_tools.0117"), lambda: mark("Approved")),
                                (tr("glossary_tools.0118"), lambda: mark("Rejected"))])
    exports = _buttons(layout, [(tr("glossary_tools.0119"), export),
                                (tr("glossary_tools.0120"), lambda: export(True))])

    def update_actions():
        for action in (tr("glossary_tools.0121"), tr("glossary_tools.0122"), tr("glossary_tools.0123")):
            actions[action].setEnabled(selected() is not None)
        exports[tr("glossary_tools.0124")].setEnabled(bool(store.entries))
        exports[tr("glossary_tools.0125")].setEnabled(any(item["status"] == "Approved" for item in store.entries.values()))

    table.itemSelectionChanged.connect(update_actions)
    refresh()
    window.resize(900, 430)
    window.exec()
    window.deleteLater()
