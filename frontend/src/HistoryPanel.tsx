import { useState } from 'react'
import { Clock3, Pencil, Search, Trash2, X } from 'lucide-react'

export type Conversation = { id:string;title:string;preview:string;count:number;status:string;updated_at:number }
type Props = {items:Conversation[];activeId:string;onOpen:(id:string)=>void;onRename:(id:string,title:string)=>Promise<void>;onDelete:(id?:string)=>Promise<void>}

export function HistoryPanel({items,activeId,onOpen,onRename,onDelete}:Props){
 const [query,setQuery]=useState(''),[dialog,setDialog]=useState<{kind:'rename'|'delete'|'clear';item?:Conversation}|null>(null)
 const [title,setTitle]=useState(''),[pending,setPending]=useState(false),[error,setError]=useState('')
 function show(kind:'rename'|'delete'|'clear',item?:Conversation){setError('');setTitle(item?.title||'');setDialog({kind,item})}
 async function apply(){if(!dialog||pending)return;setPending(true);setError('');try{if(dialog.kind==='rename')await onRename(dialog.item!.id,title.trim());else await onDelete(dialog.item?.id);setDialog(null)}catch(e){setError((e as Error).message)}finally{setPending(false)}}
 const filtered=items.filter(item=>(item.title+' '+item.preview).toLowerCase().includes(query.toLowerCase()))
 return <section className="history-panel" aria-label="历史会话">
  <div className="history-heading"><span><Clock3 size={15}/>历史会话</span><button aria-label="清空历史会话" title="清空此浏览器的历史会话" disabled={!items.length} onClick={()=>show('clear')}><Trash2 size={15}/></button></div>
  <label className="history-search"><Search size={14}/><input aria-label="搜索历史会话" placeholder="搜索对话标题或最近问题" value={query} onChange={e=>setQuery(e.target.value)}/></label>
  <div className="history-items">{filtered.map(item=><div className={'history-item '+(activeId===item.id?'current':'')} key={item.id}>
   <button className="history-select" onClick={()=>onOpen(item.id)} title={item.title}><strong>{item.title}</strong><small>{{queued:'等待开始 · ',running:'正在查询 · ',needs_confirmation:'待确认功能 · ',needs_input:'待补充信息 · ',error:'查询未完成 · '}[item.status]||''}{item.count} 次问答 · {new Date(item.updated_at*1000).toLocaleDateString('zh-CN')}</small></button>
   <div className="history-actions"><button aria-label={'重命名对话 '+item.title} title="重命名" onClick={()=>show('rename',item)}><Pencil size={13}/></button><button aria-label={'删除对话 '+item.title} title="删除" onClick={()=>show('delete',item)}><Trash2 size={13}/></button></div>
  </div>)}{!filtered.length&&<p className="history-empty">{query?'没有找到匹配的对话':'提问后自动保存在这里'}</p>}</div>
  <p className="history-policy">默认保留 · 关闭页面不会清空</p>
  {dialog&&<div className="modal-backdrop" onClick={()=>!pending&&setDialog(null)}><section role="dialog" aria-modal="true" aria-label={dialog.kind==='rename'?'重命名对话':dialog.kind==='clear'?'清空历史会话':'删除对话'} className="history-dialog" onClick={e=>e.stopPropagation()}>
   <div className="modal-head"><h2>{dialog.kind==='rename'?'重命名对话':dialog.kind==='clear'?'清空历史会话':'删除这条对话？'}</h2><button aria-label="关闭历史操作" disabled={pending} onClick={()=>setDialog(null)}><X size={20}/></button></div>
   {dialog.kind==='rename'?<label className="rename-label">对话名称<input autoFocus aria-label="对话名称" maxLength={120} value={title} onChange={e=>setTitle(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&title.trim())apply()}}/></label>:<p>{dialog.kind==='clear'?`将删除此浏览器的 ${items.length} 条历史对话及其中的问答。`:`将删除“${dialog.item?.title}”及其中的问答。`}删除后不可恢复；知识库、原始资料和模型不会被删除。</p>}
   {error&&<p role="alert" className="error-message">{error}</p>}<div className="dialog-actions"><button className="secondary" disabled={pending} onClick={()=>setDialog(null)}>取消</button><button className={dialog.kind==='rename'?'primary':'danger'} disabled={pending||(dialog.kind==='rename'&&!title.trim())} onClick={apply}>{pending?'处理中…':dialog.kind==='rename'?'保存名称':dialog.kind==='clear'?'确认清空历史':'确认删除对话'}</button></div>
  </section></div>}
 </section>
}
