/* Actual exported sklearn TF-IDF + logistic regression. No API calls. */
(function(root){
function features(text,part){
 text=text.toLowerCase();const counts=new Map();const add=s=>{const id=part.vocabulary[s];if(id!==undefined)counts.set(id,(counts.get(id)||0)+1)};
 if(part.analyzer==='word'){
  const words=text.match(/[\p{L}\p{N}_]{2,}/gu)||[];
  for(let n=part.range[0];n<=part.range[1];n++)for(let i=0;i+n<=words.length;i++)add(words.slice(i,i+n).join(' '));
 }else{
  for(const word of text.trim().split(/\s+/).filter(Boolean)){
   const w=' '+word+' ';
   for(let n=part.range[0];n<=part.range[1];n++){
    if(w.length<=n){add(w);break;}
    for(let i=0;i+n<=w.length;i++)add(w.slice(i,i+n));
   }
  }
 }
 let norm=0;for(const [id,c] of counts){const v=(1+Math.log(c))*part.idf[id];counts.set(id,v);norm+=v*v;}
 norm=Math.sqrt(norm);if(norm)for(const [id,v] of counts)counts.set(id,v/norm);return counts;
}
function predict(model,text){
 let offset=0;const vector=[];
 for(const part of model.parts){for(const [id,v] of features(text,part))vector.push([offset+id,v,part.name]);offset+=part.idf.length;}
 const logits=model.coef.map((row,i)=>model.intercept[i]+vector.reduce((s,[j,v])=>s+row[j]*v,0));const max=Math.max(...logits),exp=logits.map(v=>Math.exp(v-max)),sum=exp.reduce((a,b)=>a+b,0),probs=exp.map(v=>v/sum),order=probs.map((p,i)=>({p,i})).sort((a,b)=>b.p-a.p),top=order[0];
 offset=0;const names=[];for(const part of model.parts){for(const [s,id] of Object.entries(part.vocabulary))names[offset+id]=part.name+': '+s;offset+=part.idf.length;}
 return {label:model.classes[top.i],score:top.p,margin:top.p-order[1].p,runner_up:model.classes[order[1].i],support:vector.map(([j,v])=>({feature:names[j],contribution:v*model.coef[top.i][j]})).filter(x=>x.contribution>0).sort((a,b)=>b.contribution-a.contribution).slice(0,5),distribution:order.map(x=>({label:model.classes[x.i],score:x.p}))};
}
root.PactModel={predict};if(typeof module!=='undefined')module.exports=root.PactModel;
})(typeof window!=='undefined'?window:globalThis);
