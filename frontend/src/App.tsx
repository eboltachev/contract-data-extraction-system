import {useState} from 'react';
import {createJob, getJob, Job} from './api/client';
import {FileUploadCard} from './components/FileUploadCard';
import {ProgressPanel} from './components/ProgressPanel';
import {AgentTimeline} from './components/AgentTimeline';
import {DownloadCard} from './components/DownloadCard';

const TERMINAL_STATUSES = ['completed', 'failed', 'rolled_back'];

export default function App() {
  const [contract, setContract] = useState<File | null>(null);
  const [template, setTemplate] = useState<File | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [events, setEvents] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);

  async function start() {
    if (!contract || !template) return;
    setBusy(true);
    setEvents([]);
    const created = await createJob(contract, template);
    const initial = await getJob(created.job_id);
    setJob(initial);

    let eventSource: EventSource | null = new EventSource(`/api/v1/jobs/${created.job_id}/events`);
    const finish = () => {
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
      window.clearInterval(pollTimer);
      setBusy(false);
    };

    const pollTimer = window.setInterval(async () => {
      const latest = await getJob(created.job_id);
      setJob(latest);
      if (TERMINAL_STATUSES.includes(latest.status)) finish();
    }, 3000);

    eventSource.onmessage = async (event) => {
      const data = JSON.parse(event.data);
      setEvents((items: any[]) => [...items, data]);
      const latest = await getJob(created.job_id);
      setJob(latest);
      if (TERMINAL_STATUSES.includes(data.status)) finish();
    };

    eventSource.onerror = () => {
      // Do not mark the job as stopped: nginx/proxy can cut long SSE streams.
      // The polling fallback above continues updating progress and enables download.
      eventSource?.close();
      eventSource = null;
    };
  }

  return (
    <main>
      <header>
        <h1>Автоматизированная система извлечения данных из коммерческих договоров</h1>
        <p>Загрузите договор и Excel-шаблон — система заполнит значения по динамическим критериям.</p>
      </header>
      <section className="grid">
        <FileUploadCard title="Договор (.doc/.docx)" accept=".doc,.docx" file={contract} onChange={setContract}/>
        <FileUploadCard title="Excel-шаблон (.xls/.xlsx)" accept=".xls,.xlsx" file={template} onChange={setTemplate}/>
      </section>
      <button className="btn primary" disabled={!contract || !template || busy} onClick={start}>Запустить обработку</button>
      {job && <ProgressPanel progress={job.progress} status={job.status} action={job.current_action} error={job.error}/>} 
      <AgentTimeline events={events}/>
      <DownloadCard jobId={job?.job_id ?? null} ready={job?.status === 'completed'}/>
    </main>
  );
}
