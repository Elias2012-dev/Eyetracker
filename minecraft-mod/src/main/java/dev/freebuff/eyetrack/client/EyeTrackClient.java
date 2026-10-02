package dev.freebuff.eyetrack.client;

import com.mojang.blaze3d.platform.InputConstants;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.keymapping.v1.KeyMappingHelper;
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
    public static final String MOD_ID = "freebuff_eyetrack";

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
                "key.freebuff_eyetrack.toggle",
                InputConstants.Type.KEYSYM, GLFW.GLFW_KEY_H,
                KeyMapping.Category.MISC));
        recenterKey = KeyMappingHelper.registerKeyMapping(new KeyMapping(
                "key.freebuff_eyetrack.recenter",
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
    }

    private static void toggle(Minecraft client) {
        enabled = !enabled;
        if (enabled) {
            recenter(client);
            message(client, "Head tracking: ON  (recenter: "
                    + recenterKey.getName() + ")");
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
