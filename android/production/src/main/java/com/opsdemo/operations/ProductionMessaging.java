package com.opsdemo.operations;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Notification;
import android.content.Context;
import android.content.Intent;
import android.Manifest;
import android.os.Build;
import android.content.pm.PackageManager;
import com.google.firebase.messaging.FirebaseMessagingService;
import com.google.firebase.messaging.RemoteMessage;

public final class ProductionMessaging extends FirebaseMessagingService {
 static void channel(Context context){((NotificationManager)context.getSystemService(Context.NOTIFICATION_SERVICE)).createNotificationChannel(new NotificationChannel("production-work","Production / الإنتاج",NotificationManager.IMPORTANCE_HIGH));}
 static void notifyWork(Context context,String id,String message){
  channel(context);if(Build.VERSION.SDK_INT>=33&&context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED)return;
  Intent intent=new Intent(context,ProductionActivity.class);intent.addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP|Intent.FLAG_ACTIVITY_SINGLE_TOP);
  PendingIntent tap=PendingIntent.getActivity(context,0,intent,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
  Notification notification=new Notification.Builder(context,"production-work").setSmallIcon(R.drawable.ic_opsdemo).setContentTitle("OPSDEMO Production / الإنتاج").setContentText(message).setAutoCancel(true).setContentIntent(tap).build();
  ((NotificationManager)context.getSystemService(Context.NOTIFICATION_SERVICE)).notify(id.hashCode(),notification);
 }
 @Override public void onMessageReceived(RemoteMessage message){if(new SessionStore(this,BuildConfig.SERVER_URL).load()==null)return;String id=message.getData().get("notice_id");notifyWork(this,id==null?"production-work":id,message.getData().containsKey("message")?message.getData().get("message"):"New production work / طلب إنتاج جديد");}
 @Override public void onNewToken(String token){getSharedPreferences("production-push",0).edit().putString("token",token).apply();}
}
