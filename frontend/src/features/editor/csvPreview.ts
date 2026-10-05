export interface CsvPreview {
  rows: string[][]
  truncated: boolean
  invalid: boolean
}

const MAX_PREVIEW_CHARACTERS = 256 * 1024
const MAX_PREVIEW_ROWS = 200
const MAX_PREVIEW_COLUMNS = 40
const MAX_CELL_CHARACTERS = 4096

/** Parse a bounded, display-only CSV preview. The original source stays untouched. */
export function parseCsvPreview(source: string): CsvPreview {
  const boundedSource = source.slice(0, MAX_PREVIEW_CHARACTERS)
  const rows: string[][] = []
  let row: string[] = [], cell = '', quoted = false, quoteClosed = false
  let invalid = false, truncated = source.length > boundedSource.length

  const pushCell = () => {
    if (row.length < MAX_PREVIEW_COLUMNS) row.push(cell)
    else truncated = true
    cell = ''
    quoteClosed = false
  }
  const pushRow = () => {
    pushCell()
    if (rows.length < MAX_PREVIEW_ROWS) rows.push(row)
    else truncated = true
    row = []
  }
  const append = (character: string) => {
    if (cell.length < MAX_CELL_CHARACTERS) cell += character
    else truncated = true
  }

  for (let index = 0; index < boundedSource.length; index++) {
    const character = boundedSource[index]!
    if (quoted) {
      if (character === '"') {
        if (boundedSource[index + 1] === '"') { append('"'); index++ }
        else { quoted = false; quoteClosed = true }
      } else append(character)
      continue
    }
    if (quoteClosed && character !== ',' && character !== '\r' && character !== '\n') {
      invalid = true
      break
    }
    if (character === ',' ) pushCell()
    else if (character === '\r' || character === '\n') {
      if (character === '\r' && boundedSource[index + 1] === '\n') index++
      pushRow()
      if (rows.length >= MAX_PREVIEW_ROWS && index < boundedSource.length - 1) { truncated = true; break }
    } else if (character === '"') {
      if (cell.length !== 0) { invalid = true; break }
      quoted = true
    } else append(character)
  }
  if (!invalid && !truncated && quoted) invalid = true
  if (!invalid && rows.length < MAX_PREVIEW_ROWS && (cell.length || row.length || quoteClosed)) pushRow()
  else if (!invalid && (cell.length || row.length || quoteClosed)) truncated = true
  return { rows, truncated, invalid }
}
