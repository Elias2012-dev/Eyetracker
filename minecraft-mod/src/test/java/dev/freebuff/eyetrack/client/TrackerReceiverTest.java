package dev.freebuff.eyetrack.client;

import org.junit.jupiter.api.Test;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.nio.charset.StandardCharsets;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Speaks the tracker's JSON protocol to the mod's receiver and checks that
 * samples arrive, parse and expire exactly as the Python tracker sends them.
 */
class TrackerReceiverTest {

    private static void send(int port, String json) throws Exception {
        try (DatagramSocket sock = new DatagramSocket()) {
            byte[] data = json.getBytes(StandardCharsets.UTF_8);
            sock.send(new DatagramPacket(data, data.length,
                    InetAddress.getByName("127.0.0.1"), port));
        }
    }

    private static void await(Probe probe) throws Exception {
        long deadline = System.currentTimeMillis() + 3000;
        while (System.currentTimeMillis() < deadline) {
            if (probe.ok()) {
                return;
            }
            Thread.sleep(20);
        }
        throw new AssertionError("condition not met in time");
    }

    private interface Probe {
        boolean ok();
    }

    @Test
    void receivesRealisticTrackerPacket() throws Exception {
        TrackerReceiver rx = new TrackerReceiver(0, "127.0.0.1", 400);
        rx.start();
        try {
            // Exactly what eyetrack/outputs/udp_json.py emits.
            send(rx.localPort(),
                    "{\"v\":1,\"t\":171234.5,\"seq\":42,\"tracking\":true,"
                            + "\"yaw\":12.5,\"pitch\":-4.5,\"roll\":0.2,"
                            + "\"x\":1.2,\"y\":-0.5,\"z\":0.0}");
            await(() -> rx.latest().tracking());

            TrackerReceiver.PoseSample s = rx.latest();
            assertTrue(s.tracking());
            assertEquals(12.5f, s.yaw(), 1e-4);
            assertEquals(-4.5f, s.pitch(), 1e-4);
            assertEquals(0.2f, s.roll(), 1e-4);
            assertEquals(1.2f, s.x(), 1e-4);
            assertEquals(42L, s.seq());

            // Face lost packet flips tracking off immediately.
            send(rx.localPort(),
                    "{\"v\":1,\"seq\":43,\"tracking\":false,"
                            + "\"yaw\":12.5,\"pitch\":-4.5,\"roll\":0.2,"
                            + "\"x\":1.2,\"y\":-0.5,\"z\":0.0}");
            await(() -> !rx.latest().tracking());
        } finally {
            rx.shutdown();
        }
    }

    @Test
    void staleStreamIsMarkedLost() throws Exception {
        TrackerReceiver rx = new TrackerReceiver(0, "127.0.0.1", 150);
        rx.start();
        try {
            send(rx.localPort(),
                    "{\"v\":1,\"seq\":1,\"tracking\":true,"
                            + "\"yaw\":1,\"pitch\":2,\"roll\":3,\"x\":0,\"y\":0,\"z\":0}");
            await(() -> rx.latest().tracking());
            assertFalse(!rx.latest().tracking());

            // Tracker dies: receiver must fall back to LOST on its own.
            long deadline = System.currentTimeMillis() + 3000;
            while (rx.latest().tracking() && System.currentTimeMillis() < deadline) {
                Thread.sleep(30);
            }
            assertFalse(rx.latest().tracking(), "stale stream must be marked lost");
        } finally {
            rx.shutdown();
        }
    }

    @Test
    void malformedPacketsAreIgnored() throws Exception {
        TrackerReceiver rx = new TrackerReceiver(0, "127.0.0.1", 400);
        rx.start();
        try {
            send(rx.localPort(), "this is not json");
            send(rx.localPort(), "{}");
            Thread.sleep(150);
            assertFalse(rx.latest().tracking());
            // ...and a good packet still gets through afterwards.
            send(rx.localPort(),
                    "{\"v\":1,\"seq\":9,\"tracking\":true,"
                            + "\"yaw\":5,\"pitch\":0,\"roll\":0,\"x\":0,\"y\":0,\"z\":0}");
            await(() -> rx.latest().tracking());
        } finally {
            rx.shutdown();
        }
    }
}
