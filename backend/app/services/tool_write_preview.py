"""Dispatch human write review without treating source files as Markdown notes."""
from app.services import note_preview

WRITE_TOOLS = note_preview.WRITE_TOOLS | {'experiments.files.write'}


async def preview_write(call):
    if call.name == 'experiments.files.write':
        from app.agent.experiment_tools import preview_write as preview
    else:
        preview = note_preview.preview_write
    return await preview(call)


async def validate_preview(call, preview):
    if call.name == 'experiments.files.write':
        from app.agent.experiment_tools import validate_preview as validate
    else:
        validate = note_preview.validate_preview
    return await validate(call, preview)
