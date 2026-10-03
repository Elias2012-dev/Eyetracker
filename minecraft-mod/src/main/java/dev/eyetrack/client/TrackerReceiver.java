package dev.eyetrack.client;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.SocketTimeoutException;
import java.io.IOException;
import java.nio.charset.StandardCharsets;

/**
 * Background UDP listener for the tracker's JSON pose packets:
 * <pre>
 * {"v":1,"t":...,"seq":42,"tracking":true,
 *  "yaw":12.3,"pitch":-4.5,"roll":0.2,"x":1.2,"y":-0.5,"z":0.0}
 * </pre>
 * Angles are degrees (yaw + = head turned to the subject's left, pitch + = up),
 * translations are centimetres relative to the calibrated centre.
 */
public class TrackerReceiver extends Thread {
    /** Immutable snapshot of the newest pose. */
    public record PoseSample(boolean tracking, float yaw, float pitch, float roll,
                              float x, float y, float z, long wallMs, long seq) {
        public static final PoseSample LOST = new PoseSample(false, 0, 0, 0, 0, 0, 0, 0, -1);
    }

    private static final int MAX_PACKET = 2048;

    private final DatagramSocket socket;
    private final long staleTimeoutMs;
    private volatile PoseSample latest = PoseSample.LOST;
    private volatile boolean running = true;

    public TrackerReceiver(int port, String bindAddress, long staleTimeoutMs) {
        super("eyetrack-udp");
        setDaemon(true);
        this.staleTimeoutMs = staleTimeoutMs;
        try {
            InetAddress addr = InetAddress.getByName(bindAddress);
            socket = new DatagramSocket(new java.net.InetSocketAddress(addr, port));
            socket.setSoTimeout(500);
        } catch (Exception e) {
            throw new IllegalStateException("Cannot bind UDP port " + port + ": " + e, e);
        }
    }

    @Override
    public void run() {
        byte[] buf = new byte[MAX_PACKET];
        long lastPacketAt = System.currentTimeMillis();
        boolean complained = false;
        while (running) {
            DatagramPacket packet = new DatagramPacket(buf, buf.length);
            try {
                socket.receive(packet);
            } catch (SocketTimeoutException timeout) {
                if (System.currentTimeMillis() - lastPacketAt > staleTimeoutMs
                        && latest.tracking()) {
                    latest = PoseSample.LOST;  // tracker went quiet
                }
                continue;
            } catch (IOException e) {
                if (running) {
                    if (!complained) {
                        System.err.println("[EyeTrack] UDP receive error: " + e.getMessage());
                        complained = true;
                    }
                    try {
                        Thread.sleep(500);
                    } catch (InterruptedException ie) {
                        Thread.currentThread().interrupt();
                        break;
                    }
                }
                continue;
            }
            complained = false;
            lastPacketAt = System.currentTimeMillis();
            try {
                String json = new String(packet.getData(), packet.getOffset(),
                        packet.getLength(), StandardCharsets.UTF_8);
                PoseSample sample = parse(json, lastPacketAt);
                if (sample != null) {
                    latest = sample;
                }
            } catch (Exception ignored) {
                // never let a malformed datagram kill the listener
            }
        }
        socket.close();
    }

    private static PoseSample parse(String json, long now) {
        JsonObject o = JsonParser.parseString(json).getAsJsonObject();
        if (!o.has("yaw")) {
            return null;
        }
        boolean tracking = o.has("tracking") && o.get("tracking").getAsBoolean();
        long seq = o.has("seq") ? o.get("seq").getAsLong() : -1;
        return new PoseSample(
                tracking,
                o.get("yaw").getAsFloat(),
                o.has("pitch") ? o.get("pitch").getAsFloat() : 0f,
                o.has("roll") ? o.get("roll").getAsFloat() : 0f,
                o.has("x") ? o.get("x").getAsFloat() : 0f,
                o.has("y") ? o.get("y").getAsFloat() : 0f,
                o.has("z") ? o.get("z").getAsFloat() : 0f,
                now, seq);
    }

    /** Actual bound port (useful when constructed with port 0). */
    public int localPort() {
        return socket.getLocalPort();
    }

    /** Newest sample; degrades to LOST when older than the stale timeout. */
    public PoseSample latest() {
        PoseSample s = latest;
        if (s.tracking() && System.currentTimeMillis() - s.wallMs() > staleTimeoutMs) {
            return PoseSample.LOST;
        }
        return s;
    }

    public void shutdown() {
        running = false;
        socket.close();
    }
}
