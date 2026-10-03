package dev.eyetrack.client;

import com.mojang.blaze3d.platform.InputConstants;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.keymapping.v1.KeyMappingHelper;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayConnectionEvents;
import net.minecraft.client.KeyMapping;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.network.chat.Component;
import org.lwjgl.glfw.GLFW;

/**
 * Client-side head-tracking entry point.
 *
 * <p>The tracker streams calibrated head pose over UDP (see {@link TrackerReceiver}).
 * While tracking is enabled the mixin on {@code Minecraft.renderFrame} rewrites the
 * player's yaw/pitch every rendered frame, so the view moves smoothly with your head.
 * Mouse-look keeps working whenever tracking is disabled or the face is lost.</p>
 */
public class EyeTrackClient implements ClientModInitializer {
    public static final String MOD_ID = "eyetrack";

    /** Default bindings, spelled out for the on-screen hints. */
    private static final String KEY_TOGGLE = "H";
    private static final String KEY_RECENTER = "J";

    private static ModConfig config;
    private static TrackerReceiver receiver;
    private static KeyMapping toggleKey;
    private static KeyMapping recenterKey;

    private static boolean enabled = false;
    /** View angles the tracked pose is measured against. */
    private static float refYaw = 0f;
    private static float refPitch = 0f;

    @Override
    public void onInitializeClient() {
        config = ModConfig.load();
        receiver = new TrackerReceiver(config.port, config.bindAddress, config.staleTimeoutMs);
        receiver.start();
        System.out.println("[EyeTrack] listening for tracker pose on UDP port "
                + config.port + " (" + config.bindAddress + ")");

        toggleKey = KeyMappingHelper.registerKeyMapping(new KeyMapping(
                "key.eyetrack.toggle",
                InputConstants.Type.KEYSYM, GLFW.GLFW_KEY_H,
                KeyMapping.Category.MISC));
        recenterKey = KeyMappingHelper.registerKeyMapping(new KeyMapping(
                "key.eyetrack.recenter",
                InputConstants.Type.KEYSYM, GLFW.GLFW_KEY_J,
                KeyMapping.Category.MISC));

        ClientTickEvents.END_CLIENT_TICK.register(client -> {
            while (toggleKey.consumeClick()) {
                toggle(client);
            }
            while (recenterKey.consumeClick()) {
                recenter(client);
            }
        });

        // Without this the mod is silent: you press "Start tracking" in
        // Eyetracker, the tracker is clearly working, and Minecraft looks
        // broken because nothing happens until you guess to press H.
        ClientPlayConnectionEvents.JOIN.register((handler, sender, client) ->
                announce(client));
    }

    /** One line when a world loads: what the mod is doing and how to use it. */
    public static void announce(Minecraft client) {
        TrackerReceiver.PoseSample s = receiver.latest();
        String on = enabled ? "ON" : "OFF";
        if (s.seq() < 0) {
            // seq is -1 until a datagram has ever arrived. Say "not yet"
            // rather than "missing" - the tracker may still be starting.
            message(client, "Head tracking " + on + ". No tracker data yet on UDP "
                    + config.port + " - start Eyetracker.exe if the camera is already"
                    + " running. Press " + KEY_TOGGLE + " to toggle, "
                    + KEY_RECENTER + " to recentre.");
        } else {
            message(client, "Head tracking " + on + " (tracker found on UDP "
                    + config.port + "). Press " + KEY_TOGGLE + " to toggle, "
                    + KEY_RECENTER + " to recentre.");
        }
    }

    private static void toggle(Minecraft client) {
        enabled = !enabled;
        if (enabled) {
            TrackerReceiver.PoseSample s = receiver.latest();
            if (!s.tracking()) {
                message(client, "Head tracking: ON, but no tracker data on UDP "
                        + config.port + " yet - is Eyetracker.exe running?");
            } else {
                recenter(client);
                message(client, "Head tracking: ON (recenter: "
                        + KEY_RECENTER + ")");
            }
        } else {
            message(client, "Head tracking: OFF");
        }
    }

    /** Sync the tracked pose to wherever the camera currently looks. */
    private static void recenter(Minecraft client) {
        LocalPlayer player = client.player;
        if (player == null) {
            return;
        }
        TrackerReceiver.PoseSample s = receiver.latest();
        if (!s.tracking()) {
            refYaw = player.getYRot();
            refPitch = player.getXRot();
            return;
        }
        refYaw = wrapDegrees(player.getYRot() - curve(s.yaw(), config.yawRange, config.yawGamma)
                * config.yawSensitivity * (config.invertYaw ? -1f : 1f));
        refPitch = clampPitch(player.getXRot()
                + curve(s.pitch(), config.pitchRange, config.pitchGamma)
                * config.pitchSensitivity * (config.invertPitch ? -1f : 1f));
    }

    /**
     * Called from {@code Minecraft.renderFrame} HEAD every rendered frame.
     * Keeps the camera glued to the tracked pose while enabled.
     */
    public static void applyPose(Minecraft client) {
        if (!enabled || client == null) {
            return;
        }
        LocalPlayer player = client.player;
        if (player == null) {
            return;
        }
        TrackerReceiver.PoseSample s = receiver.latest();
        if (!s.tracking()) {
            return; // face lost / stale stream: hold the last view, mouse still works
        }
        float yaw = wrapDegrees(refYaw + curve(s.yaw(), config.yawRange, config.yawGamma)
                * config.yawSensitivity * (config.invertYaw ? -1f : 1f));
        // Tracker pitch is positive looking up; Minecraft xRot is positive looking down.
        float pitch = clampPitch(refPitch - curve(s.pitch(), config.pitchRange, config.pitchGamma)
                * config.pitchSensitivity * (config.invertPitch ? -1f : 1f));

        player.setYRot(yaw);
        player.setXRot(pitch);
        // Keep previous-tick rotations in sync so the renderer does not lag a tick.
        player.yRotO = yaw;
        player.xRotO = pitch;
    }

    /** Normalise to (-180, 180]. */
    private static float wrapDegrees(float deg) {
        deg %= 360f;
        if (deg > 180f) {
            deg -= 360f;
        } else if (deg <= -180f) {
            deg += 360f;
        }
        return deg;
    }

    private static float clampPitch(float p) {
        return Math.max(-90f, Math.min(90f, p));
    }

    /**
     * Response curve: clamp into +/-range, then {@code sign * (|v|/range)^gamma * range}.
     * gamma = 1 is linear; &lt; 1 boosts small head motions, &gt; 1 tames them.
     */
    static float curve(float v, float range, float gamma) {
        float r = Math.max(range, 1e-3f);
        float n = Math.max(-r, Math.min(r, v)) / r;
        float shaped = (float) (Math.signum(n) * Math.pow(Math.abs(n), Math.max(gamma, 0.05f)));
        return shaped * r;
    }

    private static void message(Minecraft client, String text) {
        if (client.gui != null && client.gui.hud != null) {
            client.gui.hud.setOverlayMessage(Component.literal("[EyeTrack] " + text), false);
        }
    }

    public static boolean isEnabled() {
        return enabled;
    }
}
