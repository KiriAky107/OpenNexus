"""Dispatch human write review without treating source files as Markdown notes."""
from app.services import note_preview

ACTION_TOOLS = frozenset({'experiments.run', 'experiments.import'})
WRITE_TOOLS = note_preview.WRITE_TOOLS | {'experiments.files.write'} | ACTION_TOOLS


async def preview_write(call, run_id=None, request_id=None):
    if call.name in ACTION_TOOLS:
        from app.agent.experiment_actions import preview_action
        return await preview_action(call, run_id, request_id)
    if call.name == 'experiments.files.write':
        from app.agent.experiment_tools import preview_write as preview
    else:
        preview = note_preview.preview_write
    return await preview(call)


async def validate_preview(call, preview):
    if call.name in ACTION_TOOLS:
        from app.agent.experiment_actions import validate_preview as validate
        return await validate(call, preview)
    if call.name == 'experiments.files.write':
        from app.agent.experiment_tools import validate_preview as validate
    else:
        validate = note_preview.validate_preview
    return await validate(call, preview)


async def cancel_preview(preview):
    if preview and preview.get('kind') in {'experiment_run', 'experiment_import'}:
        from app.agent.experiment_actions import cancel_preview as cancel
        await cancel(preview)
