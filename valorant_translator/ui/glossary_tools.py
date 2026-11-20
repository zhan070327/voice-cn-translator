"""Local glossary and feedback dialogs for the existing history and settings tabs."""
from __future__ import annotations

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

JSON_FILTER = "JSON 文件 (*.json)"


def dialog(parent, title):
    window = QDialog(parent)
    window.setWindowTitle(title)
    apply_dialog_theme(window)
    return window, QVBoxLayout(window)


def preview(parent, title, text, *, confirm=False):
    window, layout = dialog(parent, title)
    if confirm:
        layout.addWidget(QLabel("以下文件包含对话文字。请检查昵称、联系方式和敏感信息后再分享。"))
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
        ("时间", entry.created_at), ("游戏", GAMES.get(entry.game, entry.game)),
        ("语言方向", f"{entry.language or '未记录'} → {entry.target_language or '未记录'}"),
        ("原始识别", entry.original), ("纠错后原文", entry.corrected_original),
        ("原始模型译文", entry.machine_translation), ("最终字幕", entry.chinese),
        ("手动修正", entry.corrected_chinese), ("应用的词库规则", entry.glossary_rules),
    ]
    return "\n".join(f"{key}：{value or '未记录'}" for key, value in lines)


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
    window, layout = dialog(parent, "编辑词条")
    form = QFormLayout()
    game = QComboBox()
    for code, name in GAMES.items():
        game.addItem(name, code)
    if game.findData(rule.game) < 0:
        game.addItem(rule.game, rule.game)
    game.setCurrentIndex(game.findData(rule.game))
    source, target = QLineEdit(rule.source), QLineEdit(rule.target)
    source.setPlaceholderText("语言代码，例如 en、zh、ja")
    target.setPlaceholderText("语言代码，例如 zh、en、de")
    original, translated = QLineEdit(rule.original), QLineEdit(rule.translated)
    corrected = QLineEdit(rule.corrected)
    priority = QSpinBox()
    priority.setRange(-1000, 1000)
    priority.setValue(rule.priority)
    fields = [("游戏", game), ("源语言", source), ("目标语言", target), ("原文", original),
              ("译文", translated), ("优先级", priority)]
    if rule.kind == "asr_correction":
        fields.insert(4, ("纠错后原文", corrected))
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
                          priority=priority.value(), origin="用户自定义", verified=False, updated=now())
        try:
            selected = Rule.parse(vars(changed))
        except ValueError as exc:
            QMessageBox.warning(window, "检查词条", str(exc))
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
        preview(parent, "默认词库", f"默认词库无法读取。\n原因：{exc}")
        return
    rows = ["默认词库 · " + GAMES.get(game, game)]
    for key, title in (
        ("exact_phrases", "整句译法"), ("target_replacements", "译文替换"),
        ("location_aliases", "地图位置"), ("entity_aliases", "人物与武器别名"),
        ("translated_name_aliases", "译名修正"), ("contextual_rewrites", "语境改写"),
        ("rules", "识别纠错与台词规则"), ("suppressed_lines", "英雄台词过滤"),
        ("recognition_hotwords", "识别热词"), ("uncertain_recognition_fragments", "不确定片段"),
        ("weapon_names", "武器"), ("agent_names", "英雄")
    ):
        items = data.get(key)
        if not items:
            continue
        rows.extend(("", f"【{title} · {len(items)} 条】"))
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
        rows.append("此游戏目前没有默认词条。可切换到 Valorant 查看内置词库。")
    preview(parent, "默认词库", "\n".join(rows))


def manage_rules(parent, store):
    window, layout = dialog(parent, "自定义词库")
    layout.addWidget(QLabel("同范围冲突依次比较优先级、核实状态、更新时间。被停用的词条不参与匹配。"))
    status = QLabel()
    status.setWordWrap(True)
    layout.addWidget(status)
    table = _table(layout, ("游戏", "方向", "原文", "纠错或译文", "优先级", "状态"))

    def refresh(message=""):
        _populate(table, [(GAMES.get(rule.game, rule.game), f"{rule.source} → {rule.target}", rule.original,
                           rule.corrected or rule.translated, rule.priority, "启用" if rule.enabled else "停用")
                          for rule in store.rules])
        empty = "暂无自定义词条。点击「添加」或「导入」。" if not store.rules else ""
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
            refresh("发现同范围重复词条，按上方规则决定优先级。" if conflict else "已保存")
        except (OSError, ValueError) as exc:
            QMessageBox.warning(window, "保存失败", str(exc))

    def change(*, remove=False):
        index = select()
        if index is None:
            return
        rules = list(store.rules)
        if remove:
            if QMessageBox.question(window, "删除词条", "删除选中的自定义词条？") != QMessageBox.StandardButton.Yes:
                return
            rules.pop(index)
        else:
            rules[index] = replace(rules[index], enabled=not rules[index].enabled, updated=now())
        try:
            store.save(rules)
            refresh("已保存")
        except (OSError, ValueError) as exc:
            QMessageBox.warning(window, "保存失败", str(exc))

    def transfer(importing):
        picker = QFileDialog.getOpenFileName if importing else QFileDialog.getSaveFileName
        name, _ = picker(window, "导入或导出自定义词库", "" if importing else "我的词库.json", JSON_FILTER)
        if not name:
            return
        try:
            if importing:
                count = store.import_file(Path(name))
                refresh(f"导入完成。发现 {count} 条同范围冲突。" if count else "导入完成")
            else:
                store.export(Path(name))
                refresh("导出完成")
        except (OSError, ValueError) as exc:
            QMessageBox.warning(window, "操作失败", str(exc))

    actions = _buttons(layout, [("添加", edit), ("编辑", lambda: edit(select())), ("启用或停用", change),
                                ("删除", lambda: change(remove=True)), ("导入", lambda: transfer(True)),
                                ("导出", lambda: transfer(False))])

    def update_actions():
        for action in ("编辑", "启用或停用", "删除"):
            actions[action].setEnabled(select() is not None)

    table.itemSelectionChanged.connect(update_actions)
    refresh()
    window.resize(850, 420)
    window.exec()
    window.deleteLater()


def submit_feedback(parent, store, entry):
    window, layout = dialog(parent, "反馈翻译错误")
    form = QFormLayout()
    source = QLabel(entry.original)
    source.setTextFormat(Qt.TextFormat.PlainText)
    source.setWordWrap(True)
    form.addRow("识别原文", source)
    current = QLabel(entry.corrected_chinese or entry.chinese or "未记录")
    current.setTextFormat(Qt.TextFormat.PlainText)
    current.setWordWrap(True)
    form.addRow("当前译文", current)
    suggestion = QLineEdit()
    category = QComboBox()
    category.addItems(ERROR_TYPES)
    category.setCurrentText("普通翻译错误")
    note = QPlainTextEdit()
    note.setPlaceholderText("可选。请勿填写联系方式等个人信息。")
    form.addRow("建议译文", suggestion)
    form.addRow("错误类型", category)
    form.addRow("备注", note)
    layout.addLayout(form)
    controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)

    def save():
        try:
            added = store.add(entry, suggestion.text(), category.currentText(), note.toPlainText())
            parent.statusBar().showMessage("反馈已保存在本机" if added else "同一建议已存在")
            window.accept()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(window, "保存失败", str(exc))

    controls.accepted.connect(save)
    controls.rejected.connect(window.reject)
    layout.addWidget(controls)
    localize_buttons(window)
    window.exec()
    window.deleteLater()


def review_feedback(parent, store, custom_rules):
    import json

    window, layout = dialog(parent, "本地反馈与审核")
    hint = QLabel("导入反馈后可筛选、通过或拒绝。通过的反馈进入候选词库。")
    hint.setWordWrap(True)
    layout.addWidget(hint)
    empty = QLabel()
    empty.setWordWrap(True)
    layout.addWidget(empty)
    category = QComboBox()
    category.addItems(("全部类型", *ERROR_TYPES))
    layout.addWidget(category)
    table = _table(layout, ("审核状态", "游戏 / 语言", "原文", "建议译文", "错误类型"))
    keys = []

    def refresh():
        records = [item for item in store.entries.values()
                   if category.currentIndex() == 0 or item["category"] == category.currentText()]
        keys[:] = [item["id"] for item in records]
        _populate(table, [(item["status"], f"{item['game']} {item['source']}→{item['target']}",
                           item["original"], item["suggested"], item["category"]) for item in records])
        if store.unreadable:
            empty.setText("本机反馈文件无法读取。请备份原文件后恢复。")
        elif not store.entries:
            empty.setText("暂无反馈。点击「导入反馈」打开测试者的文件。")
        else:
            empty.setText("此分类下暂无反馈。可切换错误类型。")
        empty.setVisible(store.unreadable or not records)
        update_actions()

    def selected():
        row = table.currentRow()
        return store.entries[keys[row]] if 0 <= row < len(keys) else None

    def show():
        record = selected()
        if record:
            preview(window, "反馈详情", json.dumps(record, ensure_ascii=False, indent=2))

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
                hint.setText("已通过。与现有词条重合，导入候选前请核对。" if conflict else "已通过，已进入候选集。")
            else:
                hint.setText("已拒绝")
            refresh()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(window, "审核失败", str(exc))

    def load():
        name, _ = QFileDialog.getOpenFileName(window, "导入反馈", "", JSON_FILTER)
        if name:
            try:
                imported, duplicate = store.import_file(Path(name))
                hint.setText(f"已导入 {imported} 条，合并重复 {duplicate} 条。")
                refresh()
            except (OSError, ValueError) as exc:
                QMessageBox.warning(window, "导入失败", str(exc))

    def export(candidates=False):
        if candidates:
            data = {"version": 1, "rules": [vars(rule) for rule in store.approved_candidates()]}
        else:
            data = {"version": 1, "feedback": list(store.entries.values())}
        if not preview(window, "分享前检查", json.dumps(data, ensure_ascii=False, indent=2), confirm=True):
            return
        name, _ = QFileDialog.getSaveFileName(window, "导出文件",
                                             "已审核词库候选.json" if candidates else "翻译反馈.json", JSON_FILTER)
        if name:
            try:
                write_document(Path(name), data)
                hint.setText("文件已导出")
            except (OSError, ValueError) as exc:
                QMessageBox.warning(window, "导出失败", str(exc))

    category.currentIndexChanged.connect(refresh)
    table.doubleClicked.connect(show)
    actions = _buttons(layout, [("导入反馈", load), ("查看", show), ("通过", lambda: mark("Approved")),
                                ("拒绝", lambda: mark("Rejected"))])
    exports = _buttons(layout, [("导出反馈与审核结果", export),
                                ("导出已审核词库候选", lambda: export(True))])

    def update_actions():
        for action in ("查看", "通过", "拒绝"):
            actions[action].setEnabled(selected() is not None)
        exports["导出反馈与审核结果"].setEnabled(bool(store.entries))
        exports["导出已审核词库候选"].setEnabled(any(item["status"] == "Approved" for item in store.entries.values()))

    table.itemSelectionChanged.connect(update_actions)
    refresh()
    window.resize(900, 430)
    window.exec()
    window.deleteLater()
