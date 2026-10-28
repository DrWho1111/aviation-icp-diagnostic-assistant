type Snapshot = {status:string}

/** SSE first; keep checking the persisted task if the stream disconnects. */
export function monitorRun<T extends Snapshot>(options:{url:string;fetchSnapshot:()=>Promise<T>;onUpdate:(run:T)=>void;onError:(message:string)=>void}){
 let cancelled=false, polling=false, failures=0
 let timer:ReturnType<typeof setTimeout>|undefined
 const source=new EventSource(options.url)
 function stop(){cancelled=true;source.close();if(timer)clearTimeout(timer)}
 function receive(run:T){
  if(cancelled)return
  options.onUpdate(run)
  if(!['queued','running'].includes(run.status))stop()
 }
 async function poll(){
  if(cancelled)return
  try{const run=await options.fetchSnapshot();if(cancelled)return;failures=0;receive(run)}
  catch{if(cancelled)return;if(++failures>=3){stop();options.onError('进度连接暂时中断。任务仍保存在服务端，可重新打开此对话查看结果。');return}}
  if(!cancelled)timer=setTimeout(poll,1000)
 }
 function fallback(){if(cancelled||polling)return;polling=true;source.close();void poll()}
 source.addEventListener('update',event=>{try{receive(JSON.parse((event as MessageEvent).data))}catch{fallback()}})
 source.onerror=fallback
 return stop
}
