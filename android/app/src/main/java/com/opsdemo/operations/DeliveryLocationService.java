package com.opsdemo.operations;

import android.app.*;
import android.content.*;
import android.content.pm.ServiceInfo;
import android.location.*;
import android.os.*;
import org.json.JSONObject;
import java.time.Instant;
import java.util.concurrent.*;

/** Visible, route-scoped sharing. Latest sample only; no offline breadcrumb queue. */
public class DeliveryLocationService extends Service implements LocationListener {
    static volatile ApiClient transport;
    static volatile boolean running;
    private final Handler handler=new Handler(Looper.getMainLooper());
    private final ExecutorService io=Executors.newSingleThreadExecutor();
    private LocationManager locations;
    private long lastVerified;
    private boolean ended;
    private final Runnable poll=new Runnable(){public void run(){
        if(ended)return;
        if(lastVerified>0&&SystemClock.elapsedRealtime()-lastVerified>120000){stopSelf();return;}
        io.submit(()->{try{
            ApiClient client=transport;if(client==null){stopSelf();return;}
            JSONObject status=client.get("/api/v1/courier/location/");
            if(!status.optBoolean("active")){stopSelf();return;}
            lastVerified=SystemClock.elapsedRealtime();
            handler.post(()->requestFix());
        }catch(Exception e){if(e instanceof ApiClient.ApiException&&(((ApiClient.ApiException)e).status==401||((ApiClient.ApiException)e).status==403))stopSelf();}});
        handler.postDelayed(this,30000);
    }};
    @Override public void onCreate(){
        super.onCreate();locations=(LocationManager)getSystemService(LOCATION_SERVICE);
        NotificationManager manager=getSystemService(NotificationManager.class);
        manager.createNotificationChannel(new NotificationChannel("delivery_location","Delivery location / موقع التوصيل",NotificationManager.IMPORTANCE_LOW));
        PendingIntent open=PendingIntent.getActivity(this,0,new Intent(this,MainActivity.class),PendingIntent.FLAG_IMMUTABLE|PendingIntent.FLAG_UPDATE_CURRENT);
        Notification notice=new Notification.Builder(this,"delivery_location").setSmallIcon(R.drawable.status_calendar)
            .setContentTitle("OPSDEMO · موقع التوصيل / Delivery location")
            .setContentText("Sharing during active deliveries · مشاركة الموقع أثناء التوصيل")
            .setContentIntent(open).setOngoing(true).build();
        try{if(Build.VERSION.SDK_INT>=29)startForeground(903,notice,ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION);else startForeground(903,notice);}
        catch(SecurityException e){stopSelf();}
    }
    @Override public int onStartCommand(Intent intent,int flags,int id){
        if(transport==null){stopSelf();return START_NOT_STICKY;}
        if(!running){running=true;lastVerified=SystemClock.elapsedRealtime();handler.post(poll);}
        return START_NOT_STICKY;
    }
    @SuppressWarnings("MissingPermission") private void requestFix(){
        if(ended)return;
        try{
            locations.removeUpdates(this);
            // Approximate permission supports network fixes. Request both available
            // providers with precise permission so indoor GPS does not block updates.
            boolean precise=checkSelfPermission(android.Manifest.permission.ACCESS_FINE_LOCATION)==android.content.pm.PackageManager.PERMISSION_GRANTED;
            if(locations.isProviderEnabled(LocationManager.NETWORK_PROVIDER))locations.requestSingleUpdate(LocationManager.NETWORK_PROVIDER,this,Looper.getMainLooper());
            if(precise&&locations.isProviderEnabled(LocationManager.GPS_PROVIDER))locations.requestSingleUpdate(LocationManager.GPS_PROVIDER,this,Looper.getMainLooper());
        }catch(SecurityException e){stopSelf();}catch(IllegalArgumentException unavailable){/* Next poll can try again. */}
    }
    @Override public void onLocationChanged(Location fix){
        if(ended||SystemClock.elapsedRealtime()-lastVerified>120000)return;
        if(System.currentTimeMillis()-fix.getTime()>120000)return;
        io.submit(()->{if(ended)return;try{
            JSONObject body=new JSONObject().put("latitude",fix.getLatitude()).put("longitude",fix.getLongitude())
                .put("accuracy",fix.hasAccuracy()?fix.getAccuracy():10000).put("sampled_at",Instant.ofEpochMilli(fix.getTime()).toString());
            if(!transport.post("/api/v1/courier/location/",body).optBoolean("active"))stopSelf();
        }catch(Exception ignored){/* No offline queue; the next fresh sample replaces this one. */}});
    }
    @Override public void onProviderDisabled(String provider){}
    @Override public void onProviderEnabled(String provider){}
    @Override public void onStatusChanged(String provider,int status,Bundle extras){}
    @Override public IBinder onBind(Intent intent){return null;}
    @Override public void onDestroy(){ended=true;running=false;handler.removeCallbacksAndMessages(null);if(locations!=null)locations.removeUpdates(this);io.shutdownNow();stopForeground(STOP_FOREGROUND_REMOVE);super.onDestroy();}
}
