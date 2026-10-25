package com.opsdemo.operations;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import org.json.JSONObject;
import java.security.KeyStore;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import java.nio.charset.StandardCharsets;

/** Stores only a server session token, encrypted with a non-exportable Android Keystore key. */
final class SessionStore {
    private final String alias;
    private final SharedPreferences preferences;
    private final byte[] origin;
    SessionStore(Context context,String server){this(context,server,"remembered-session");}
    SessionStore(Context context,String server,String storageName){preferences=context.getSharedPreferences(storageName,Context.MODE_PRIVATE);origin=server.getBytes(StandardCharsets.UTF_8);alias="opsdemo."+storageName+".v1";}
    private SecretKey key(boolean create)throws Exception{
        KeyStore store=KeyStore.getInstance("AndroidKeyStore");store.load(null);
        if(store.containsAlias(alias))return (SecretKey)store.getKey(alias,null);
        if(!create)throw new IllegalStateException("Session key unavailable");
        KeyGenerator generator=KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,"AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(alias,KeyProperties.PURPOSE_ENCRYPT|KeyProperties.PURPOSE_DECRYPT).setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).setKeySize(256).build());
        return generator.generateKey();
    }
    void save(String token,long expiresAt)throws Exception{
        JSONObject data=new JSONObject().put("token",token).put("expires",expiresAt);
        Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.ENCRYPT_MODE,key(true));cipher.updateAAD(origin);
        byte[] encrypted=cipher.doFinal(data.toString().getBytes(StandardCharsets.UTF_8));
        JSONObject envelope=new JSONObject().put("iv",Base64.encodeToString(cipher.getIV(),Base64.NO_WRAP)).put("data",Base64.encodeToString(encrypted,Base64.NO_WRAP));
        if(!preferences.edit().putString("encrypted",envelope.toString()).commit())throw new IllegalStateException("Could not save session");
    }
    JSONObject load(){
        String value=preferences.getString("encrypted",null);if(value==null)return null;
        try{
            JSONObject envelope=new JSONObject(value);
            Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.DECRYPT_MODE,key(false),new GCMParameterSpec(128,Base64.decode(envelope.getString("iv"),Base64.NO_WRAP)));cipher.updateAAD(origin);
            JSONObject data=new JSONObject(new String(cipher.doFinal(Base64.decode(envelope.getString("data"),Base64.NO_WRAP)),StandardCharsets.UTF_8));
            if(data.getLong("expires")<=System.currentTimeMillis()||data.getString("token").isEmpty())throw new IllegalStateException("Expired session");
            return data;
        }catch(Exception error){clear();return null;}
    }
    void clear(){
        preferences.edit().remove("encrypted").commit();
        try{KeyStore store=KeyStore.getInstance("AndroidKeyStore");store.load(null);if(store.containsAlias(alias))store.deleteEntry(alias);}catch(Exception ignored){}
    }
}
