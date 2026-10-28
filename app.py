import os,math
from datetime import datetime
from functools import wraps
import pandas as pd, requests
from flask import Flask,jsonify,redirect,render_template,request,session,send_file
app=Flask(__name__); app.secret_key=os.getenv("SECRET_KEY",os.getenv("JWT_SECRET","change-me"))
AO_CLIENT_ID=os.getenv("AO_CLIENT_ID",""); AO_CLIENT_SECRET=os.getenv("AO_CLIENT_SECRET",""); AO_REDIRECT_URI=os.getenv("AO_REDIRECT_URI","")
AO_SCOPE=os.getenv("AO_SCOPE","bank_transfer_save"); AO_BT_BULK_SAVE_PATH=os.getenv("AO_BT_BULK_SAVE_PATH","/api/bank-transfer/bulk-save.do")
AUTH_URL="https://account.accurate.id/oauth/authorize"; TOKEN_URL="https://account.accurate.id/oauth/token"; DB_LIST_URL="https://account.accurate.id/api/db-list.do"; OPEN_DB_URL="https://account.accurate.id/api/open-db.do"
APP_LOGIN_EMAIL=os.getenv("APP_LOGIN_EMAIL","demo@aca-aol.id"); APP_LOGIN_PASSWORD=os.getenv("APP_LOGIN_PASSWORD","1234")
def reqlogin(fn):
 @wraps(fn)
 def w(*a,**k):
  if not session.get("logged_in"): return redirect("/login")
  return fn(*a,**k)
 return w
def blank(v): return v is None or (isinstance(v,float) and math.isnan(v)) or str(v).strip()==""
def txt(v): return "" if blank(v) else str(v).strip()
def num(v,required=False):
 if blank(v):
  if required: raise ValueError("nilai angka wajib diisi")
  return None
 try:return float(v)
 except: raise ValueError(f"nilai angka tidak valid: {v}")
def integer(v):
 if blank(v): return None
 x=float(v)
 if not x.is_integer(): raise ValueError(f"harus angka bulat: {v}")
 return int(x)
def datefmt(v):
 if blank(v): return None
 if isinstance(v,pd.Timestamp): d=v.to_pydatetime()
 elif isinstance(v,datetime): d=v
 else:d=pd.to_datetime(v,dayfirst=True,errors="raise").to_pydatetime()
 return d.strftime("%d/%m/%Y")
def put(d,k,v):
 if v is not None and v!="": d[k]=v
def unwrap(p): return p.get("d",p) if isinstance(p,dict) else p
def norm(v):
 if isinstance(v,list): return v
 if isinstance(v,dict) and isinstance(v.get("data"),list): return v["data"]
 raise ValueError("Hasil builder Bank Transfer tidak valid")
HEAD=["TRANSDATE","FROMBANKNO","TOBANKNO","FROMBANKAMOUNT","DESCRIPTION","BRANCHID","BRANCHNAME","TOBRANCHNAME","FROMBANKRATE","TOBANKRATE","DIFFERENCEACCOUNTNO","TYPEAUTONUMBER"]
def consistent(g,fields,label):
 first=g.iloc[0]
 for f in fields:
  b=txt(first.get(f))
  for _,r in g.iloc[1:].iterrows():
   if txt(r.get(f))!=b: raise ValueError(f"{label}: field {f} harus konsisten")
def build_transactions(df):
 df=df.copy(); df.columns=[str(c).strip().upper() for c in df.columns]
 req=["NUMBER","TRANSDATE","FROMBANKNO","TOBANKNO","FROMBANKAMOUNT","DETAILTYPE"]; miss=[x for x in req if x not in df.columns]
 if miss: raise ValueError("Kolom wajib tidak ditemukan: "+", ".join(miss))
 out=[]
 for number,g in df.groupby("NUMBER",sort=False,dropna=False):
  number=txt(number)
  if not number: raise ValueError("NUMBER tidak boleh kosong")
  g=g.reset_index(drop=True); consistent(g,HEAD,number); r=g.iloc[0]
  td=datefmt(r.get("TRANSDATE")); fb=txt(r.get("FROMBANKNO")); tb=txt(r.get("TOBANKNO")); amt=num(r.get("FROMBANKAMOUNT"),True)
  if not td: raise ValueError(f"{number}: TRANSDATE wajib")
  if not fb: raise ValueError(f"{number}: FROMBANKNO wajib")
  if not tb: raise ValueError(f"{number}: TOBANKNO wajib")
  if amt<=0: raise ValueError(f"{number}: FROMBANKAMOUNT harus > 0")
  tx={"number":number,"transDate":td,"fromBankNo":fb,"toBankNo":tb,"fromBankAmount":amt,"detailBankTransfer":[]}
  for col,key,conv in [("DESCRIPTION","description",txt),("BRANCHID","branchId",integer),("BRANCHNAME","branchName",txt),("TOBRANCHNAME","toBranchName",txt),("FROMBANKRATE","fromBankRate",num),("TOBANKRATE","toBankRate",num),("DIFFERENCEACCOUNTNO","differenceAccountNo",txt),("TYPEAUTONUMBER","typeAutoNumber",integer)]: put(tx,key,conv(r.get(col)))
  dtype=g["DETAILTYPE"].fillna("").astype(str).str.strip().str.upper()
  bad=g[~dtype.isin(["NONE","CHARGE"])]
  if not bad.empty: raise ValueError(f"{number}: DETAILTYPE harus NONE atau CHARGE")
  for idx,row in g[dtype=="CHARGE"].iterrows():
   ac=txt(row.get("ACCOUNTNO")); a=num(row.get("AMOUNT"),True); ct=txt(row.get("BANKCHARGETYPE")).upper(); en=txt(row.get("EXPENSENAME"))
   if not ac: raise ValueError(f"{number} baris {idx+2}: ACCOUNTNO wajib")
   if a<=0: raise ValueError(f"{number} baris {idx+2}: AMOUNT harus > 0")
   if ct not in ("SOURCE_BANK","DESTINATION_BANK"): raise ValueError(f"{number} baris {idx+2}: BANKCHARGETYPE tidak valid")
   if not en: raise ValueError(f"{number} baris {idx+2}: EXPENSENAME wajib")
   d={"accountNo":ac,"amount":a,"bankChargeType":ct,"expenseName":en}
   put(d,"memo",txt(row.get("MEMO"))); put(d,"departmentName",txt(row.get("DEPARTMENT")))
   for n in range(1,11): put(d,f"dataClassification{n}Name",txt(row.get(f"DATACLASSIFICATION{n}NAME")))
   tx["detailBankTransfer"].append(d)
  out.append(tx)
 return out
def to_form(txs):
 form={}
 for i,tx in enumerate(txs):
  p=f"data[{i}]"
  for k,v in tx.items():
   if k=="detailBankTransfer": continue
   form[f"{p}.{k}"]=str(v)
  for j,d in enumerate(tx.get("detailBankTransfer",[])):
   q=f"{p}.detailBankTransfer[{j}]"
   for k,v in d.items(): form[f"{q}.{k}"]=str(v)
 return form
def tokens(): return session.get("ao_tokens") or {}
def save_tokens(v): session["ao_tokens"]=v; session.modified=True
def refresh():
 rt=tokens().get("refresh_token")
 if not rt: raise RuntimeError("Refresh token tidak tersedia")
 r=requests.post(TOKEN_URL,data={"grant_type":"refresh_token","refresh_token":rt,"client_id":AO_CLIENT_ID,"client_secret":AO_CLIENT_SECRET},timeout=30); r.raise_for_status(); t=r.json(); t.setdefault("refresh_token",rt); save_tokens(t)
def reopen():
 dbid=session.get("db_id")
 if not dbid: raise RuntimeError("Database Accurate belum dipilih")
 h={"Authorization":f"Bearer {tokens().get('access_token','')}"}
 r=requests.get(OPEN_DB_URL,params={"id":dbid},headers=h,timeout=30)
 if r.status_code==401: refresh(); h={"Authorization":f"Bearer {tokens().get('access_token','')}"}; r=requests.get(OPEN_DB_URL,params={"id":dbid},headers=h,timeout=30)
 r.raise_for_status(); db=unwrap(r.json())
 if isinstance(db,list): db=db[0] if db else {}
 if not isinstance(db,dict): raise RuntimeError("Format response open-db tidak dikenali")
 session["db_host"]=db.get("host"); session["db_session_id"]=db.get("session") or db.get("sessionId")
def headers():
 h={"Authorization":f"Bearer {tokens().get('access_token','')}"}
 if session.get("db_session_id"): h["X-Session-ID"]=session["db_session_id"]
 return h
def apost(path,form):
 if not session.get("db_host") or not session.get("db_session_id"): reopen()
 url=session["db_host"].rstrip("/")+"/accurate"+path; r=requests.post(url,data=form,headers=headers(),timeout=90); body=r.text or ""
 if r.status_code==401: refresh(); reopen(); url=session["db_host"].rstrip("/")+"/accurate"+path; r=requests.post(url,data=form,headers=headers(),timeout=90)
 elif any(x in body.lower() for x in ["data session key tidak tepat","session key tidak tepat","invalid session"]): reopen(); url=session["db_host"].rstrip("/")+"/accurate"+path; r=requests.post(url,data=form,headers=headers(),timeout=90)
 return r,url
@app.route("/login",methods=["GET","POST"])
def login():
 if request.method=="POST":
  if request.form.get("email")==APP_LOGIN_EMAIL and request.form.get("password")==APP_LOGIN_PASSWORD: session["logged_in"]=True; return redirect("/")
  return render_template("login.html",error="Email atau password salah")
 return render_template("login.html")
@app.route("/logout")
def logout(): session.clear(); return redirect("/login")
@app.route("/")
@reqlogin
def index(): return render_template("index.html",db_name=session.get("db_name"))
@app.route("/oauth/connect")
@reqlogin
def connect(): return redirect(requests.Request("GET",AUTH_URL,params={"client_id":AO_CLIENT_ID,"response_type":"code","redirect_uri":AO_REDIRECT_URI,"scope":AO_SCOPE}).prepare().url)
@app.route("/oauth/callback")
@reqlogin
def callback():
 code=request.args.get("code"); r=requests.post(TOKEN_URL,data={"grant_type":"authorization_code","code":code,"redirect_uri":AO_REDIRECT_URI,"client_id":AO_CLIENT_ID,"client_secret":AO_CLIENT_SECRET},timeout=30); r.raise_for_status(); save_tokens(r.json()); return redirect("/databases")
@app.route("/databases")
@reqlogin
def dbs():
 h={"Authorization":f"Bearer {tokens().get('access_token','')}"}; r=requests.get(DB_LIST_URL,headers=h,timeout=30)
 if r.status_code==401: refresh(); h={"Authorization":f"Bearer {tokens().get('access_token','')}"}; r=requests.get(DB_LIST_URL,headers=h,timeout=30)
 r.raise_for_status(); d=unwrap(r.json()); d=[d] if isinstance(d,dict) else d; d=d if isinstance(d,list) else []; return render_template("databases.html",databases=d)
@app.route("/open-db/<dbid>")
@reqlogin
def odb(dbid): session["db_id"]=dbid; reopen(); session["db_name"]=dbid; return redirect("/")
@app.route("/download-template")
@app.route("/api/template")
@reqlogin
def tpl(): return send_file(os.path.join(app.root_path,"template-bank-transfer.xlsx"),as_attachment=True,download_name="template-bank-transfer.xlsx")
@app.route("/api/build",methods=["POST"])
@reqlogin
def build():
 try: txs=norm(build_transactions(pd.read_excel(request.files["file"]))); return jsonify({"ok":True,"count":len(txs),"transactions":txs[:20]})
 except Exception as e:return jsonify({"ok":False,"error":str(e)}),400
@app.route("/api/import",methods=["POST"])
@reqlogin
def imp():
 try:
  txs=norm(build_transactions(pd.read_excel(request.files["file"]))); results=[]
  for start in range(0,len(txs),100):
   form=to_form(txs[start:start+100]); r,url=apost(AO_BT_BULK_SAVE_PATH,form)
   try: body=r.json()
   except: body={"raw":r.text}
   session["last_debug"]={"url":url,"status":r.status_code,"form_sample":dict(list(form.items())[:200]),"response":body}; results.append({"batch":start//100+1,"ok":r.ok,"status":r.status_code,"response":body})
   if not r.ok: break
  return jsonify({"ok":all(x["ok"] for x in results),"total":len(txs),"results":results})
 except Exception as e:return jsonify({"ok":False,"error":str(e)}),400
@app.route("/api/debug-last")
@reqlogin
def debug(): return jsonify(session.get("last_debug") or {})
if __name__=="__main__": app.run(host="0.0.0.0",port=int(os.getenv("PORT","8080")))
