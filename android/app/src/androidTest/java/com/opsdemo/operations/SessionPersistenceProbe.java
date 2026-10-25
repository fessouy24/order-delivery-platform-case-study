package com.opsdemo.operations;

import android.app.Instrumentation;
import android.app.Activity;
import android.os.Bundle;
import android.content.Context;

/** No credentials or real-account state: separate test preferences and Keystore alias. */
public final class SessionPersistenceProbe extends Instrumentation {
    private String phase;
    @Override public void onCreate(Bundle args){super.onCreate(args);phase=args.getString("phase","read");start();}
    private void check(boolean condition,String message){if(!condition)throw new AssertionError(message);}
    @Override public void onStart(){
        Bundle result=new Bundle();
        try{
            Context context=getTargetContext();String origin="http://localhost:8080";
            SessionStore store=new SessionStore(context,origin,"session-test-only");
            if(phase.equals("write")){
                store.clear();store.save("dummytestsession123",System.currentTimeMillis()+3600000);
                String saved=context.getSharedPreferences("session-test-only",0).getString("encrypted","");
                check(!saved.contains("dummytestsession123"),"Token must not be stored in plaintext");
            }else{
                check(store.load()!=null,"Encrypted session must survive process restart");
                check(store.load().getString("token").equals("dummytestsession123"),"Stored session must match");
                ApiClient api=new ApiClient(origin);api.restoreSession(store.load().getString("token"),store.load().getLong("expires"));
                check(api.cookieSnapshot().size()==1&&api.cookieSnapshot().get(0).isHttpOnly(),"Restored session must be HttpOnly");
                check(new SessionStore(context,"https://other.invalid","session-test-only").load()==null,"Another server cannot decrypt the session");
                store.save("dummytestsession123",System.currentTimeMillis()-1000);check(store.load()==null,"Expired session must be cleared");
                store.save("dummytestsession123",System.currentTimeMillis()+3600000);context.getSharedPreferences("session-test-only",0).edit().putString("encrypted","tampered").commit();check(store.load()==null,"Tampered session must be cleared");
                store.save("dummytestsession123",System.currentTimeMillis()+3600000);store.clear();check(store.load()==null,"Sign out must clear persisted session");
            }
            result.putString("stream","PASS: remembered-session "+phase+"\n");finish(Activity.RESULT_OK,result);
        }catch(Throwable error){result.putString("stream","FAIL: "+error.getClass().getSimpleName()+": "+error.getMessage()+"\n");finish(Activity.RESULT_CANCELED,result);}
    }
}
