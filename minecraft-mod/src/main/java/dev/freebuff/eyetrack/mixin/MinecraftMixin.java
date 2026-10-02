package dev.freebuff.eyetrack.mixin;

import dev.freebuff.eyetrack.client.EyeTrackClient;
import net.minecraft.client.Minecraft;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Runs before every rendered frame so the tracked head pose is applied after
 * mouse input of the tick but before the camera picks up the player rotation.
 */
@Mixin(Minecraft.class)
public class MinecraftMixin {
    @Inject(method = "renderFrame", at = @At("HEAD"))
    private void eyetrack$applyPoseBeforeFrame(boolean renderLevel, CallbackInfo ci) {
        EyeTrackClient.applyPose((Minecraft) (Object) this);
    }
}
