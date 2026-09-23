"""Run-local references separate model context from storage identifiers."""
from copy import deepcopy


class NoteReferences:
    FIELDS = {
        "note_id": "note", "note_ids": "note", "block_id": "block",
        "citation_id": "source", "content_hash": "revision",
        "expected_content_hash": "revision",
    }

    def __init__(self):
        self._values: dict[str, dict[str, str]] = {}

    def _translate(self, value, kind, *, restore):
        if isinstance(value, list):
            return [self._translate(item, kind, restore=restore) for item in value]
        if not isinstance(value, str) or not value:
            return value
        values = self._values.setdefault(kind, {})
        if restore:
            return next((raw for raw, alias in values.items() if alias == value), value)
        if value not in values:
            values[value] = f"{kind}:{len(values) + 1}"
        return values[value]

    def transform(self, value, *, restore=False):
        if isinstance(value, list):
            return [self.transform(item, restore=restore) for item in value]
        if isinstance(value, dict):
            return {
                key: self._translate(item, self.FIELDS[key], restore=restore)
                if key in self.FIELDS else self.transform(item, restore=restore)
                for key, item in value.items()
            }
        return deepcopy(value)

    @classmethod
    def tool_parameters(cls, schema):
        schema = deepcopy(schema)
        def visit(node):
            if not isinstance(node, dict):
                return
            for name, field in node.get("properties", {}).items():
                if name in cls.FIELDS:
                    field.pop("pattern", None)
                    field["description"] = "Pass the temporary reference returned by a previous tool result."
                visit(field)
            for value in node.values():
                if isinstance(value, dict):
                    visit(value)
                elif isinstance(value, list):
                    for item in value:
                        visit(item)
        visit(schema)
        return schema


REFERENCE_INSTRUCTIONS = (
    "引用笔记时使用笔记标题、文件路径或 [1] 形式的来源编号，绝不把内部 ID 或内容哈希写入正文、标题或新文件名。"
    "工具返回的 note:1、block:1、revision:1 等是本次运行的临时操作引用，调用工具时原样传回，"
    "不要推算、展示或编造这些引用。source:1 对应来源编号 [1]。"
)
