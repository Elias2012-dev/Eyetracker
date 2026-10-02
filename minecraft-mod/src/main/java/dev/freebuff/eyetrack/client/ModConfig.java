package dev.freebuff.eyetrack.client;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import net.fabricmc.loader.api.FabricLoader;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

/** Config file: config/freebuff_eyetrack.json (created with defaults on first run). */
public class ModConfig {
    private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();

    /** UDP port the tracker streams pose packets to. */
    public int port = 47777;
    /** Bind address: 127.0.0.1 for this PC only, 0.0.0.0 to allow LAN tracking. */
    public String bindAddress = "127.0.0.1";
    /** Consider tracking lost when no packet arrived for this long. */
    public long staleTimeoutMs = 500;

    public float yawSensitivity = 1.0f;
    public float pitchSensitivity = 1.0f;
    /** Comfortable head-turn range in degrees (curve normalisation). */
    public float yawRange = 40f;
    public float pitchRange = 30f;
    /** Response curve exponent: <1 more sensitive near centre, >1 calmer. */
    public float yawGamma = 1.0f;
    public float pitchGamma = 1.0f;
    public boolean invertYaw = false;
    public boolean invertPitch = false;

    public static Path path() {
        return FabricLoader.getInstance().getConfigDir().resolve("freebuff_eyetrack.json");
    }

    public static ModConfig load() {
        Path file = path();
        if (Files.exists(file)) {
            try {
                ModConfig cfg = GSON.fromJson(Files.readString(file), ModConfig.class);
                if (cfg != null) {
                    return cfg;
                }
            } catch (Exception ignored) {
                // fall through to defaults
            }
        }
        ModConfig cfg = new ModConfig();
        cfg.save();
        return cfg;
    }

    public void save() {
        try {
            Files.writeString(path(), GSON.toJson(this));
        } catch (IOException e) {
            System.err.println("[EyeTrack] could not write config: " + e.getMessage());
        }
    }
}
