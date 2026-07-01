export type Job={job_id:string;status:string;progress:number;current_action:string;error:string|null;created_at:string;updated_at:string};
export async function createJob(contract:File, template:File){const f=new FormData();f.append('contract_file',contract);f.append('template_file',template);const r=await fetch('/api/v1/jobs',{method:'POST',body:f}); if(!r.ok) throw new Error(await r.text()); return r.json();}
export async function getJob(id:string):Promise<Job>{const r=await fetch(`/api/v1/jobs/${id}`); if(!r.ok) throw new Error(await r.text()); return r.json();}
export function downloadUrl(id:string){return `/api/v1/jobs/${id}/download`;}
