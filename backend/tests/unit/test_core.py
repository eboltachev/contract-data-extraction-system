from pathlib import Path
import pytest
from openpyxl import Workbook, load_workbook
from app.infrastructure.parsers.excel_template import read_criteria, fill_template
from app.infrastructure.repositories.file_repository import output_filename, FileRepository
from app.domain.extraction import ExtractionResult
from app.agents.base import BaseAgent
from app.domain.jobs import Job
from app.services.pipeline import Pipeline


def make_template(path: Path):
    wb=Workbook(); ws=wb.active; ws.title='Лист1'; ws.append(['№','Критерий','Значение']); ws.append([1,'Номер договора',None]); ws.append([2,'Дата подписания договора',None]); wb.save(path)

def test_read_criteria_and_columns(tmp_path):
    p=tmp_path/'t.xlsx'; make_template(p)
    _,_,val_col,criteria=read_criteria(p)
    assert val_col==3 and [c.name for c in criteria]==['Номер договора','Дата подписания договора']

def test_fill_template(tmp_path):
    p=tmp_path/'t.xlsx'; out=tmp_path/'out.xlsx'; make_template(p)
    fill_template(p,out,[ExtractionResult(criterion='Номер договора', value='ABC-1')])
    ws=load_workbook(out).active
    assert ws['C2'].value=='ABC-1'

def test_output_filename():
    assert output_filename('Договор №1.docx')=='Договор №1.xlsx'

@pytest.mark.asyncio
async def test_agent_iteration_fallback(monkeypatch):
    monkeypatch.setattr('app.core.config.settings.AGENT_MAX_ITERATIONS', 2, raising=False)
    a=BaseAgent(); a.max_iterations=2; calls=0
    async def nope():
        nonlocal calls; calls+=1; return None
    assert await a.bounded(nope, 'fallback')=='fallback' and calls==2

def test_fallback_value_present():
    r=ExtractionResult(criterion='X', value='Не найдено в договоре. Требует ручной проверки.')
    assert 'ручной проверки' in r.value

@pytest.mark.asyncio
async def test_rollback_on_stage_error(tmp_path):
    repo=type('R',(),{'job_dir':lambda self,j: tmp_path, 'save':lambda self,job: None})()
    async def save(job): pass
    repo.save=save
    files=FileRepository(); await files.prepare_dirs(tmp_path); (tmp_path/'output'/'partial.xlsx').write_text('x')
    job=Job(job_id='j')
    async def bad(): raise RuntimeError('boom')
    await Pipeline(repo, files).run(job, tmp_path/'missing.docx', tmp_path/'missing.xlsx')
    assert job.status=='failed'
