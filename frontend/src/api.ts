export async function api<T>(path:string,body?:unknown):Promise<T>{
 const res=await fetch('/api'+path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 const data=await res.json();if(!res.ok)throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));return data;
}
export function displayValue(value:boolean|number|null){return value===null?'N/A':typeof value==='boolean'?(value?'Да':'Нет'):String(value)}
export function filtered<T extends {conversation_id:string;language:string;channel:string;dataset:string}>(rows:T[],query:string,lang:string,channel:string,dataset:string){return rows.filter(r=>r.conversation_id.toLowerCase().includes(query.toLowerCase())&&(!lang||r.language===lang)&&(!channel||r.channel===channel)&&(!dataset||r.dataset===dataset))}
