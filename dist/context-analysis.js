/* Context review layer, v2. Deterministic English evidence extraction, not a generative model.
   Keeps raw ML separate; no output authenticates a payment or applies an event. */
(function(root){
const rules=[
 ['payment_failure',/\b(?:payment|transfer|settlement|funds?|money)\b.{0,45}\b(?:fail(?:ed|ure)?|declined|rejected|reversed|returned|bounced back|unpaid)\b|\b(?:bank|provider)\b.{0,25}\b(?:declined|rejected|reversed)\b.{0,30}\b(?:payment|transfer|settlement)\b/i],
 ['payment_promise',/\b(?:i|we)(?:'ll| will| intend to| plan to)\s+(?:pay|settle|transfer|send the money)\b|\bpayment\s+(?:is |has been )?scheduled\b|\b(?:i|we)\s+will\s+pay\b/i],
 ['payment_notice',/\b(?:i|we)(?:'ve| have)?\s+(?:paid|sent the (?:money|payment)|transferred)\b|\b(?:payment|transfer|invoice|money)\b.{0,25}\b(?:completed|went through|(?:is|was|has been) paid|received|sent)\b|\b(?:paid|settled)\s+(?:the |your |our )?invoice\b/i],
 ['agreement',/\b(?:i|we)\s+(?:approve|accept|confirm)\b.{0,45}\b(?:quote|proposal|estimate|scope|price|work|package|offer)\b|\b(?:go ahead|please proceed|please begin|let's proceed)\b|\byour proposal works for us\b/i],
 ['delivery',/\b(?:final|finished|completed)\s+(?:images|files|designs|work|documents|deliverables)\b.{0,35}\b(?:attached|ready|uploaded|available)\b|\b(?:uploaded|attached|delivered)\b.{0,25}\b(?:completed|final|finished)\b|\b(?:files|images|designs)\s+are attached\b|\beverything is ready\b/i],
 ['rejection',/\b(?:we|i)\s+(?:are |am )?(?:cancell?ing|cancell?ed|decline|reject)\b|\b(?:we|i)(?: have)?\s+(?:chose|chosen|selected)\s+(?:another|a different)\b|\b(?:please )?stop (?:work|working)\b|\bwill not proceed\b/i],
 ['offer',/\b(?:please |can you |could you )?quote\s+(?:for|the|this)\b|\b(?:how much|what would it cost|our budget|maximum available fee)\b/i],
 ['admin',/\b(?:takes over|replaces|new point of contact|new contact|delivery date|move (?:the )?(?:delivery|deadline)|timezone|keep the original scope|ignore my earlier request)\b/i]
];
function amountsIn(text){
 const found=[],issues=[];
 const numeric=raw=>{
  if(/[ \u00a0]/.test(raw)&&!/^\d{1,3}(?:[ \u00a0]\d{3})+(?:[.,]\d{1,2})?$/.test(raw))return null;let s=raw.replace(/[ \u00a0]/g,'');if(s[0]==='-')return null;
  if(s.includes(',')&&s.includes('.')){
   if(/^\d{1,3}(?:,\d{3})+\.\d{1,2}$/.test(s))s=s.replace(/,/g,'');
   else if(/^\d{1,3}(?:\.\d{3})+,\d{1,2}$/.test(s))s=s.replace(/\./g,'').replace(',','.');
   else return null;
  }else if(/[.,]/.test(s)){
   if(/^\d+[.,]\d{1,2}$/.test(s))s=s.replace(',','.');
   else if(/^\d{1,3}(?:,\d{3}){2,}$/.test(s))s=s.replace(/,/g,'');
   else return null; // A single 3-digit group is ambiguous across locales.
  }
  const value=Number(s);return Number.isFinite(value)&&value>=0&&value<=1e9?value:null;
 };
 const re=/(?<![\w-])(?:\b(EUR|USD|GBP)\s*|([€$£])\s*)(-?\d(?:[\d., \u00a0]*\d)?)|(?<![\w-])(-?\d(?:[\d., \u00a0]*\d)?)\s*(EUR|USD|GBP)\b/gi;
 for(const m of text.matchAll(re)){const currency=(m[1]||m[5]||({'€':'EUR','$':'USD','£':'GBP'}[m[2]])).toUpperCase(),raw=m[3]||m[4],value=numeric(raw);if(value===null)issues.push('Ambiguous or unsupported amount: '+m[0]);else found.push({currency,value,source:m[0]});}
 return {amounts:found,issues};
}
function analyze(text,raw){
 text=String(text||'');if(text.length>10000)return {status:'needs_review',events:[],ignored:[],warnings:['Message exceeds the 10,000-character review limit.'],raw,confirmedReceived:0,requiresReview:true};
 const warnings=[],ignored=[],events=[];let masked=text.replace(/[’‘]/g,"'");
 // Preserve offsets by replacing excluded characters with spaces, not by deleting them.
 const hide=(re,reason)=>{masked=masked.replace(re,(s,offset)=>{ignored.push({quote:text.slice(offset,offset+s.length),start:offset,end:offset+s.length,reason});return s.replace(/[^\n]/g,' ')});};
 hide(/^\s*(?:[A-Z]{2,5}-\d{2,}:\s*)?>[^\n]*/gm,'quoted history');hide(/(?:On [^\n]+wrote:|-----Original Message-----|Begin forwarded message:)[\s\S]*/gi,'forwarded history');
 hide(/"[^"\n]*"|“[^”\n]*”/g,'quoted text');
 if(/\b(?:example|test message|sample message|hypothetical|just a draft)\b/i.test(masked))warnings.push('Example, test or draft language: no event extracted.');
 if(/\b(?:ignore previous instructions|mark (?:the )?(?:invoice|order) (?:paid|approved))\b/i.test(masked))warnings.push('Instruction to change records is not evidence of an event.');
 if(/["“”]/.test(masked))warnings.push('Unclosed quotation: review the original message before extracting events.');const stop=warnings.length>0;
 // Split discourse clauses so negation in one clause does not erase a distinct assertion.
 const chunks=/[\s\S]+?(?:[.!?;](?=\s|$)|\n|$)/g;let match;
 while((match=chunks.exec(masked))){let sentence=match[0];let clauses=[{s:sentence,at:match.index}];if(!/\b(?:if|unless|provided|subject to)\b/i.test(sentence)&&/[, ]+\s*(?:but|however|although)\s+/i.test(sentence)){const b=sentence.match(/[, ]+\s*(?:but|however|although)\s+/i);clauses=[{s:sentence.slice(0,b.index),at:match.index},{s:sentence.slice(b.index+b[0].length),at:match.index+b.index+b[0].length}];}
 for(const clause of clauses){const trim=clause.s.trim();if(!trim)continue;const start=clause.at+clause.s.indexOf(trim),end=start+trim.length;const quote=text.slice(start,end);let labels=rules.filter(([,re])=>re.test(trim)).map(([l])=>l);if(labels.includes('agreement'))labels=labels.filter(x=>x!=='offer');if(!labels.length)continue;
 const neg=/\b(?:not|never|cannot|can't|haven't|hasn't|didn't|don't|won't|unpaid|unapproved)\b/i.test(trim);
 const conditional=/\b(?:if|unless|provided|subject to|would|might|could|pending approval|once)\b/i.test(trim);
 const question=/\?\s*$/.test(trim)||/^(?:have|has|did|do|does|can|could|will|would|is|are)\s/i.test(trim);
 const reported=/\b(?:said|says|wrote|according to|claims? that|told me)\b/i.test(trim);
 const future=/\b(?:will|going to|plan to)\b/i.test(trim);
 let reason=stop?'draft, example or instruction':neg&&!/\b(?:returned unpaid|will not proceed)\b/i.test(trim)?'negated assertion':conditional&&!labels.every(x=>['offer','admin'].includes(x))?'conditional or tentative statement':reported?'reported statement':null;
 if(question)labels=labels.filter(l=>l==='offer'||l==='admin');
 if(future)labels=labels.filter(l=>!['payment_notice','delivery','agreement'].includes(l));
 if(reason){ignored.push({quote,start,end,reason});continue;}
 for(const label of labels){const parsed=amountsIn(quote),amounts=parsed.amounts;warnings.push(...parsed.issues);const orderIds=[...new Set(quote.match(/\b[A-Z]{2,5}-\d{2,}\b/g)||[])];if(orderIds.length>1)warnings.push('Multiple orders in one clause: amounts are not automatically assigned.');events.push({label,quote,start,end,amounts,orderIds,certainty:label==='payment_notice'?'claim_only':'explicit_text',requiresReview:true});}
 }}
 const labels=[...new Set(events.map(e=>e.label))];if(labels.includes('payment_notice')&&labels.includes('payment_failure'))warnings.push('Payment claim and failure coexist: inspect chronology and order references; no receipt is confirmed.');
 if(labels.includes('agreement')&&labels.includes('rejection'))warnings.push('Approval and cancellation coexist: resolve scope and chronology before changing an agreement.');
 if(ignored.length)warnings.push('Quoted, conditional, negated or reported material was excluded; inspect the retained source.');
 if(!labels.length)warnings.push('No unambiguous supported event found. Review manually; do not infer agreement from silence.');
 if(raw&&!labels.includes(raw.label))warnings.push('Raw model suggestion is unsupported or disagrees with the contextual evidence.');
 return {status:labels.length===1?labels[0]:labels.length?'multiple_events':'needs_review',events,ignored,warnings,raw:raw||null,confirmedReceived:0,requiresReview:true,basis:'context_rules_v2'};
}
root.PactContext={analyze,amountsIn};if(typeof module!=='undefined')module.exports=root.PactContext;
})(typeof window!=='undefined'?window:globalThis);
