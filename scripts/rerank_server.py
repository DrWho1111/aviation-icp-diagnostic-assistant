"""Local BGE cross-encoder service using the RAGFlow HuggingFace rerank protocol."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

MODEL_PATH=os.getenv("RERANK_MODEL_PATH","/models/bge-reranker-v2-m3")
torch.set_num_threads(int(os.getenv("RERANK_CPU_THREADS","4")))
tokenizer=AutoTokenizer.from_pretrained(MODEL_PATH,local_files_only=True,trust_remote_code=False)
model=AutoModelForSequenceClassification.from_pretrained(MODEL_PATH,local_files_only=True,trust_remote_code=False).eval()


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass

    def respond(self,status,value):
        body=json.dumps(value,ensure_ascii=False,allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers();self.wfile.write(body)

    def do_GET(self):
        self.respond(200,{"status":"ready","model":"BAAI/bge-reranker-v2-m3","device":"cpu"}) if self.path=="/health" else self.respond(404,{"error":"not_found"})

    def do_POST(self):
        if self.path!="/rerank": return self.respond(404,{"error":"not_found"})
        try:
            size=int(self.headers.get("Content-Length","0"))
            if not 0<size<=256000: raise ValueError()
            request=json.loads(self.rfile.read(size))
            query=request["query"];texts=request["texts"]
            if not isinstance(query,str) or not 0<len(query)<=12000 or not isinstance(texts,list) or not 1<=len(texts)<=32:
                raise ValueError()
            if any(not isinstance(t,str) or len(t)>24000 for t in texts): raise ValueError()
        except (ValueError,TypeError,KeyError):
            return self.respond(400,{"error":"invalid_request"})
        try:
            scores=[]
            with torch.inference_mode():
                for start in range(0,len(texts),2):
                    batch=tokenizer([[query,t] for t in texts[start:start+2]],padding=True,truncation=True,max_length=512,return_tensors="pt")
                    logits=model(**batch,return_dict=True).logits.view(-1).float()
                    scores.extend((logits if request.get("raw_scores") else logits.sigmoid()).tolist())
            self.respond(200,[{"index":i,"score":score} for i,score in enumerate(scores)])
        except Exception:
            self.respond(500,{"error":"model_inference_failed"})


if __name__=="__main__":
    print("Local BGE reranker loaded on CPU; listening on port 9390.",flush=True)
    HTTPServer(("0.0.0.0",9390),Handler).serve_forever()
