import { BookOpen, CheckCircle2, CircleHelp, FileSearch } from 'lucide-react'

type Issue = {code:string;description:string;document_id?:string;page?:number;review?:{note:string;ocr_required:boolean}}
const names:Record<string,string>={PFL_NOT_PROVIDED:'缺少 PFL 清单',SYSTEM_REQUIREMENTS_NOT_PROVIDED:'缺少设备正式需求',LINK_ONLY:'只有外部链接',LOW_TEXT_PAGE:'页面文字较少',OCR_PAGE_REVIEWED:'已补全并校对页面文字',PDF_CROP_PATCH:'已修正页面裁剪'}
const hints:Record<string,string>={PFL_NOT_PROVIDED:'需要补充原始清单；当前查询会明确提示未提供。',SYSTEM_REQUIREMENTS_NOT_PROVIDED:'不能用方案里的燃油示例或项目协议代替设备需求。',LINK_ONLY:'原包只有百科网址，没有正文，因此未作为全文知识导入。',LOW_TEXT_PAGE:'可能是封面、插图或扫描页，需查看原页后判断是否补充 OCR。',PDF_CROP_PATCH:'已排除第一页中不属于该论文的内容，并保留原始文件。'}

export function KnowledgeIssues({issues,documents}:{issues:Issue[];documents:{id:string;name:string}[]}){
 const groups=[{title:'原始资料缺项',description:'这些内容原包未提供。系统会说明缺失，不会生成不存在的资料。',codes:['PFL_NOT_PROVIDED','SYSTEM_REQUIREMENTS_NOT_PROVIDED','LINK_ONLY'],icon:CircleHelp},
 {title:'PDF 页面核对与补全',description:'区分空白页、前置信息和正文图像。已补全文字的页面保留校对记录；原图中不可见的内容不猜填。',codes:['LOW_TEXT_PAGE','OCR_PAGE_REVIEWED'],icon:FileSearch},
 {title:'已应用的处理修正',description:'记录已经执行的处理，便于追溯，不是待修复报错。',codes:['PDF_CROP_PATCH'],icon:CheckCircle2}]
 return <section className="knowledge-issues"><h2>资料覆盖与处理说明</h2><p className="page-intro">这里记录资料边界和解析情况，不是系统故障列表。缺少的材料需要补充，有疑问的页面可以直接打开核对。</p>
 {groups.map(group=>{const items=issues.filter(i=>group.codes.includes(i.code));return <details className="issue-group" key={group.title} open={group.title!=='PDF 页面核对与补全'}><summary><group.icon size={19}/><strong>{group.title}</strong><span>{items.length} 项</span></summary><p>{group.description}</p><div>{items.map((item,n)=><article className="issue-entry" key={n}><div><strong>{names[item.code]||item.code}</strong>{item.document_id&&<span>{documents.find(d=>d.id===item.document_id)?.name||item.document_id}{item.page?` · 物理页 ${item.page}`:''}</span>}<p>{item.review?.note||hints[item.code]||item.description}</p><details className="issue-technical"><summary>查看登记详情</summary><code>{item.code}</code><p>{item.description}</p></details></div>{item.document_id&&<a href={'/api/sources/'+item.document_id+(item.page?'#page='+item.page:'')} target="_blank" rel="noreferrer"><BookOpen size={16}/>{item.page?'查看原页':'查看原件'}</a>}</article>)}</div></details>})}
 </section>
}
