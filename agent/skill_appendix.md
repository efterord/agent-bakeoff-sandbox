
## Pilot parity helpers
- `python scripts/pptx_inspect.py deck.pptx --view shapes|layouts|theme`: shape ids, geometry (inches), fonts, colours; layout names; theme fonts and colours.
- `python scripts/pptx_shape_ops.py deck.pptx --ops ops.json [--output out.pptx]`: shape edits (set_text, set_font, set_fill, move_resize, add_textbox, add_shape, delete_shape, table_add_row, table_set_cell); see `--help`.
- `python scripts/pptx_check.py deck.pptx --overflow` or `--validate`: text overflow / out-of-bounds estimate; package validity and security findings.
