"""Small, privacy-conscious page observations shared by both KUBA engines."""

from __future__ import annotations

import json
import re
from typing import Any


def observation_script(query: str | None = None, mode: str = "compact") -> str:
    query_json = json.dumps((query or "").strip(), ensure_ascii=False)
    text_limit = 8_000 if mode == "detailed" else 2_000
    element_limit = 80 if mode == "detailed" else 30
    return f"""(()=>{{
const query={query_json};
const visible=e=>{{if(!e||!e.getClientRects().length)return false;const s=getComputedStyle(e);return s.display!=='none'&&s.visibility!=='hidden'&&s.opacity!=='0'}};
const name=e=>String(e.getAttribute('aria-label')||e.labels?.[0]?.innerText||e.innerText||e.textContent||e.getAttribute('placeholder')||e.getAttribute('title')||'').trim().replace(/\\s+/g,' ').slice(0,240);
const role=e=>e.getAttribute('role')||({{BUTTON:'button',A:'link',INPUT:(e.type==='checkbox'?'checkbox':e.type==='radio'?'radio':e.type==='submit'?'button':e.type==='search'?'searchbox':'textbox'),TEXTAREA:'textbox',SELECT:'combobox'}}[e.tagName]||'');
const safeUrl=value=>{{try{{const u=new URL(value,location.href);for(const k of [...u.searchParams.keys()])if(/token|secret|password|auth|session|credential|api.?key/i.test(k))u.searchParams.set(k,'[redacted]');return u.href}}catch{{return value}}}};
const selectorFor=e=>{{if(e.id){{const s='#'+CSS.escape(e.id);if(document.querySelectorAll(s).length===1)return s}}const testId=e.getAttribute('data-testid');if(testId){{const s='[data-testid="'+CSS.escape(testId)+'"]';if(document.querySelectorAll(s).length===1)return s}}const path=[];while(e&&e.nodeType===1&&e!==document.documentElement){{let part=e.localName;if(e.id){{part+='#'+CSS.escape(e.id);path.unshift(part);break}}const parent=e.parentElement;if(parent){{const same=[...parent.children].filter(x=>x.localName===e.localName);if(same.length>1)part+=':nth-of-type('+(same.indexOf(e)+1)+')'}}path.unshift(part);e=parent}}return path.join(' > ')||null}};
const tokens=query.toLowerCase().split(/\\s+/).filter(Boolean);
const score=value=>tokens.reduce((n,t)=>n+(value.toLowerCase().includes(t)?1:0),0);
const candidates=[...document.querySelectorAll('button,a[href],input,textarea,select,[role=button],[role=link],[role=tab],[role=checkbox],[role=radio],[contenteditable=true]')].filter(visible).map((e,index)=>{{const label=name(e);const r=role(e);const detail={{index,tag:e.tagName.toLowerCase(),role:r,name:label,placeholder:e.getAttribute('placeholder')||'',type:e.getAttribute('type')||'',href:e.tagName==='A'?safeUrl(e.href):'',testId:e.getAttribute('data-testid')||'',selector:selectorFor(e),visible:true,enabled:!e.disabled&&e.getAttribute('aria-disabled')!=='true'}};return {{detail,rank:score([r,label,detail.placeholder,detail.href].join(' '))}}}}).sort((a,b)=>b.rank-a.rank).slice(0,{element_limit}).map(x=>x.detail);
const fields=[...document.querySelectorAll('input,textarea,select')].filter(visible).slice(0,{element_limit}).map(e=>({{tag:e.tagName.toLowerCase(),type:e.getAttribute('type')||'',name:e.getAttribute('name')||'',label:name(e.labels?.[0]||e),placeholder:e.getAttribute('placeholder')||'',required:!!e.required,disabled:!!e.disabled}}));
const summarize=e=>({{role:e.getAttribute('role')||'',name:(e.innerText||e.textContent||'').trim().replace(/\\s+/g,' ').slice(0,400)}});
const alerts=[...document.querySelectorAll('[role=alert],[aria-live=assertive]')].filter(visible).slice(0,10).map(summarize);
const dialogs=[...document.querySelectorAll('[role=dialog],[aria-modal=true]')].filter(visible).slice(0,10).map(summarize);
const loading=[...document.querySelectorAll('[aria-busy=true],[role=progressbar]')].filter(visible).slice(0,10).map(summarize);
const body=(document.body?.innerText||'').trim();
return {{url:safeUrl(location.href),title:document.title,readyState:document.readyState,query,visibleText:body.slice(0,{text_limit}),visibleTextTruncated:body.length>{text_limit},interactiveElements:candidates,formFields:fields,dialogs,alerts,loadingIndicators:loading}};
}})()"""


def semantic_locator_script(
    *,
    role: str | None = None,
    name: str | None = None,
    label: str | None = None,
    placeholder: str | None = None,
    text: str | None = None,
    test_id: str | None = None,
) -> str:
    criteria = {
        key: value.strip().casefold()
        for key, value in {
            "role": role,
            "name": name,
            "label": label,
            "placeholder": placeholder,
            "text": text,
            "test_id": test_id,
        }.items()
        if value and value.strip()
    }
    if not criteria:
        raise ValueError("Provide at least one semantic locator field")
    return f"""(()=>{{
const q={json.dumps(criteria, ensure_ascii=False)};
const norm=s=>String(s||'').replace(/\\s+/g,' ').trim().toLowerCase();
const visible=e=>{{if(!e.getClientRects().length)return false;const s=getComputedStyle(e),r=e.getBoundingClientRect();return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'&&s.opacity!=='0'}};
const role=e=>e.getAttribute('role')||({{BUTTON:'button',A:'link',INPUT:(e.type==='checkbox'?'checkbox':e.type==='radio'?'radio':e.type==='submit'?'button':e.type==='search'?'searchbox':'textbox'),TEXTAREA:'textbox',SELECT:'combobox'}}[e.tagName]||'');
const name=e=>norm(e.getAttribute('aria-label')||e.labels?.[0]?.innerText||e.innerText||e.textContent||e.getAttribute('placeholder')||e.getAttribute('title'));
const selectorFor=e=>{{if(e.id){{const s='#'+CSS.escape(e.id);if(document.querySelectorAll(s).length===1)return s}}const t=e.getAttribute('data-testid');if(t){{const s='[data-testid="'+CSS.escape(t)+'"]';if(document.querySelectorAll(s).length===1)return s}}const path=[];while(e&&e.nodeType===1&&e!==document.documentElement){{let p=e.localName;if(e.id){{p+='#'+CSS.escape(e.id);path.unshift(p);break}}const parent=e.parentElement;if(parent){{const siblings=[...parent.children].filter(x=>x.localName===e.localName);if(siblings.length>1)p+=':nth-of-type('+(siblings.indexOf(e)+1)+')'}}path.unshift(p);e=parent}}return path.join(' > ')||null}};
const all=[...document.querySelectorAll('button,a[href],input,textarea,select,[role=button],[role=link],[role=tab],[role=checkbox],[role=radio],[contenteditable=true]')].filter(visible);
const matches=all.filter(e=>{{const label=norm(e.labels?.[0]?.innerText);const values={{role:norm(role(e)),name:name(e),label,placeholder:norm(e.getAttribute('placeholder')),text:norm(e.innerText||e.textContent),test_id:norm(e.getAttribute('data-testid'))}};return Object.entries(q).every(([k,v])=>values[k]===v)}});
if(!matches.length)return {{error:'No visible element matched the semantic locator',count:0}};
if(matches.length!==1)return {{error:'Semantic locator is ambiguous',count:matches.length,candidates:matches.slice(0,10).map(e=>({{role:role(e),name:name(e)}}))}};
const e=matches[0];if(e.disabled||e.getAttribute('aria-disabled')==='true')return {{error:'Matched element is disabled',count:1}};
return {{count:1,selector:selectorFor(e),role:role(e),name:name(e)}};
}})()"""


def summarize_accessibility(nodes: list[dict[str, Any]], query: str | None = None, limit: int = 80) -> list[dict[str, Any]]:
    tokens = [token for token in re.split(r"\s+", (query or "").casefold()) if token]
    summarized = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict) or node.get("ignored"):
            continue
        role = node.get("role", "")
        name = node.get("name", "")
        if isinstance(role, dict):
            role = role.get("value", "")
        if isinstance(name, dict):
            name = name.get("value", "")
        if not role and not name:
            continue
        focusable = False
        for prop in node.get("properties", []):
            if not isinstance(prop, dict) or prop.get("name") != "focusable":
                continue
            value = prop.get("value", {})
            focusable = bool(value.get("value")) if isinstance(value, dict) else bool(value)
            break
        item = {
            "index": index,
            "role": str(role)[:80],
            "name": str(name)[:240],
            "focusable": focusable,
        }
        searchable = f"{item['role']} {item['name']}".casefold()
        item["relevance"] = sum(token in searchable for token in tokens)
        summarized.append(item)
    summarized.sort(key=lambda item: (-item["relevance"], item["index"]))
    for item in summarized:
        item.pop("relevance", None)
    return summarized[:limit]
