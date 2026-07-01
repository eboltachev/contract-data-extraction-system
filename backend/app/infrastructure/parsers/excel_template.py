from pathlib import Path
from openpyxl import load_workbook
from openpyxl.styles import Alignment
from app.domain.criteria import Criterion
from app.domain.extraction import ExtractionResult
from app.core.exceptions import ValidationError

def find_template_table(workbook):
    for ws in workbook.worksheets:
        for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row,20)):
            vals=[str(c.value).strip() if c.value is not None else "" for c in row]
            if "Критерий" in vals and "Значение" in vals and "№" in vals:
                return ws, row[0].row, vals.index("Критерий")+1, vals.index("Значение")+1
    raise ValidationError("В шаблоне не найдены заголовки №, Критерий, Значение")

def read_criteria(path: Path) -> tuple[object, object, int, list[Criterion]]:
    wb=load_workbook(path)
    ws, header_row, crit_col, val_col=find_template_table(wb)
    criteria=[]
    for r in range(header_row+1, ws.max_row+1):
        v=ws.cell(r, crit_col).value
        if v and str(v).strip(): criteria.append(Criterion(row=r, name=str(v).strip()))
    return wb, ws, val_col, criteria

def fill_template(template: Path, output: Path, results: list[ExtractionResult]) -> None:
    wb, ws, val_col, criteria=read_criteria(template)
    by={r.criterion:r for r in results}
    for c in criteria:
        cell=ws.cell(c.row, val_col); res=by.get(c.name)
        cell.value=res.value if res else "Не найдено в договоре. Требует ручной проверки."
        cell.alignment=Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[c.row].height=max(ws.row_dimensions[c.row].height or 18, min(120, 18 + (str(cell.value).count('\n')*14)))
    output.parent.mkdir(parents=True, exist_ok=True); wb.save(output)
