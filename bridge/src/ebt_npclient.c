/*
 * ebt_npclient.c - Freebuff EyeTrack "game link" bridge for ETS2 / ATS.
 *
 * This is our own implementation of the NPClient API that TrackIR-aware
 * games (including SCS's Euro Truck Simulator 2 / American Truck Simulator)
 * load through the registry key
 *
 *     HKCU\Software\NaturalPoint\NATURALPOINT\NPClient Location  (Path)
 *
 * The game calls NP_GetData() in this DLL; the DLL reads head pose from the
 * EyeTrack shared memory block ("EBT_GameLink_v1") that the tracker writes.
 * No other tracker software is involved anywhere.
 *
 * The exported function set, the tir_data layout, the checksum and the
 * signature encoding are dictated by the game-facing TrackIR client ABI
 * (the same behavior implemented by wine/linuxtrack's public NPClient).
 * Scale: angles degrees -> +/-16383 over +/-180 deg; translation +/-16383
 * over +/-50 cm.  Games expose per-axis scale/invert, so exact ranges are
 * adjustable in-game.
 *
 * Build: bridge\\build.bat (TinyCC, produces a 64-bit DLL; ETS2/ATS are
 * 64-bit).  Ship the result as both NPClient.dll and NPClient64.dll.
 */

#include <windows.h>
#include <stdbool.h>

/* ---- shared memory (MUST match eyetrack/outputs/game_link.py) ---------- */

#define EBT_LINK_NAME  "EBT_GameLink_v1"
#define EBT_LINK_MUTEX "EBT_GameLink_v1_Mutex"

typedef struct EBTLink {
    unsigned int frame_id;      /* bumped by the writer every frame      */
    unsigned int tracking;      /* 1 = pose is fresh, 0 = face lost      */
    float yaw_deg;              /* + = turned to the user's LEFT         */
    float pitch_deg;            /* + = looking UP                        */
    float roll_deg;
    float x_cm;                 /* +right, +up, +away-from-screen        */
    float y_cm;
    float z_cm;
    int game_id;                /* written by the game via NP_* call     */
    int game_id_ack;            /* echoed back by the tracker            */
    unsigned int reserved[4];
} EBTLink;                      /* 56 bytes */

/* ---- TrackIR client ABI ------------------------------------------------ */

#define NP_AXIS_MAX 16383

typedef struct tir_data {
    short status;               /* 0 = tracking, 1 = not tracking       */
    short frame;
    unsigned cksum;
    float roll, pitch, yaw;
    float tx, ty, tz;
    float padding[9];
} tir_data_t;

typedef struct tir_signature {
    char DllSignature[200];
    char AppSignature[200];
} tir_signature_t;

#define NP_DECLSPEC __declspec(dllexport)
#define NP_EXPORT(t) t NP_DECLSPEC __stdcall

static HANDLE g_mapping = NULL;
static EBTLink volatile *g_link = NULL;
static HANDLE g_mutex = NULL;

static bool ebt_create_mapping(void)
{
    if (g_link)
        return true;

    g_mutex = CreateMutexA(NULL, FALSE, EBT_LINK_MUTEX);
    g_mapping = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE,
                                   0, sizeof(EBTLink), EBT_LINK_NAME);
    if (!g_mapping)
        return false;
    /* read/write: the game writes its profile id through this view */
    g_link = (EBTLink volatile *)MapViewOfFile(g_mapping, FILE_MAP_WRITE,
                                               0, 0, sizeof(EBTLink));
    return g_link != NULL;
}

/* Checksum over the pose block, as required by the TrackIR client ABI. */
static unsigned ebt_cksum(unsigned char buf[], unsigned size)
{
    int rounds, rem, c, a0, a2;

    if (size == 0 || buf == NULL)
        return 0;

    rounds = (int)(size >> 2);
    rem = (int)(size % 4);
    c = (int)size;

    while (rounds != 0) {
        a0 = *(short int *)buf;
        a2 = *(short int *)(buf + 2);
        buf += 4;
        c += a0;
        a2 ^= (c << 5);
        a2 <<= 11;
        c ^= a2;
        c += (c >> 11);
        --rounds;
    }
    switch (rem) {
    case 3:
        a0 = *(short int *)buf;
        a2 = *(signed char *)(buf + 2);
        c += a0;
        a2 = (a2 << 2) ^ c;
        c ^= (a2 << 16);
        a2 = (c >> 11);
        break;
    case 2:
        a2 = *(short int *)buf;
        c += a2;
        c ^= (c << 11);
        a2 = (c >> 17);
        break;
    case 1:
        a2 = *(signed char *)buf;
        c += a2;
        c ^= (c << 10);
        a2 = (c >> 1);
        break;
    default:
        a2 = 0;
        break;
    }
    if (rem != 0)
        c += a2;

    c ^= (c << 3);
    c += (c >> 5);
    c ^= (c << 4);
    c += (c >> 17);
    c ^= (c << 25);
    c += (c >> 6);

    return (unsigned)c;
}

static double ebt_clamp(double v, double lo, double hi)
{
    return v > hi ? hi : (v < lo ? lo : v);
}

/* ---- exports ----------------------------------------------------------- */

BOOL WINAPI DllMain(HINSTANCE hinst, DWORD reason, LPVOID reserved)
{
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(hinst);
    } else if (reason == DLL_PROCESS_DETACH) {
        if (g_link) {
            UnmapViewOfFile((void *)g_link);
            g_link = NULL;
        }
        if (g_mapping) {
            CloseHandle(g_mapping);
            g_mapping = NULL;
        }
        if (g_mutex) {
            CloseHandle(g_mutex);
            g_mutex = NULL;
        }
    }
    return TRUE;
}

NP_EXPORT(int) NP_GetData(tir_data_t *data)
{
    unsigned int frame = 0, tracking = 0;
    double yaw = 0, pitch = 0, roll = 0, tx = 0, ty = 0, tz = 0;
    int i;

    if (!ebt_create_mapping() || g_link == NULL || data == NULL)
        return 1; /* not tracking */

    if (WaitForSingleObject(g_mutex, 50) == WAIT_OBJECT_0) {
        yaw = g_link->yaw_deg;
        pitch = g_link->pitch_deg;
        roll = g_link->roll_deg;
        tx = g_link->x_cm;
        ty = g_link->y_cm;
        tz = g_link->z_cm;
        frame = g_link->frame_id;
        tracking = g_link->tracking;
        ReleaseMutex(g_mutex);
    } else {
        return 1;
    }

    /* Scale first, clamp second: TCC 0.9.27 miscompiles an inline FP
     * expression passed as the first double argument (it receives the
     * second argument's value), so the call must pass plain locals. */
    yaw = yaw / 180.0 * NP_AXIS_MAX;
    pitch = pitch / 180.0 * NP_AXIS_MAX;
    roll = roll / 180.0 * NP_AXIS_MAX;
    tx = tx * (NP_AXIS_MAX / 50.0);
    ty = ty * (NP_AXIS_MAX / 50.0);
    tz = tz * (NP_AXIS_MAX / 50.0);

    yaw = ebt_clamp(yaw, -NP_AXIS_MAX, NP_AXIS_MAX);
    pitch = ebt_clamp(pitch, -NP_AXIS_MAX, NP_AXIS_MAX);
    roll = ebt_clamp(roll, -NP_AXIS_MAX, NP_AXIS_MAX);
    tx = ebt_clamp(tx, -NP_AXIS_MAX, NP_AXIS_MAX);
    ty = ebt_clamp(ty, -NP_AXIS_MAX, NP_AXIS_MAX);
    tz = ebt_clamp(tz, -NP_AXIS_MAX, NP_AXIS_MAX);

    data->status = tracking ? 0 : 1;
    data->frame = (short)(frame & 0xFFFF);
    data->roll = (float)roll;
    data->pitch = (float)pitch;
    data->yaw = (float)yaw;
    data->tx = (float)tx;
    data->ty = (float)ty;
    data->tz = (float)tz;
    for (i = 0; i < 9; ++i)
        data->padding[i] = 0.0f;

    data->cksum = 0; /* checksum runs over the block with cksum == 0 */
    data->cksum = ebt_cksum((unsigned char *)data, sizeof(tir_data_t));
    return tracking ? 0 : 1;
}

/*
 * Signature blobs: games may call NP_GetSignature and compare the strings.
 * The byte arrays are the established client-ABI values (identical to the
 * public wine/linuxtrack implementation games already accept); they are
 * generated byte-exactly from the reference into ebt_signatures.h.
 */
#include "ebt_signatures.h"

NP_EXPORT(int) NP_GetSignature(tir_signature_t *sig)
{
    unsigned i;
    if (sig == NULL)
        return 1;
    for (i = 0; i < 200; i++) {
        sig->DllSignature[i] = (char)(part1_2[i] ^ part1_1[i]);
        sig->AppSignature[i] = (char)(part2_1[i] ^ part2_2[i]);
    }
    return 0;
}

NP_EXPORT(int) NP_QueryVersion(unsigned short *version)
{
    if (version)
        *version = 0x0500;
    return 0;
}

NP_EXPORT(int) NP_ReCenter(void)
{
    return 0;
}

NP_EXPORT(int) NP_RegisterProgramProfileID(unsigned short id)
{
    if (ebt_create_mapping() && g_link) {
        if (WaitForSingleObject(g_mutex, 50) == WAIT_OBJECT_0) {
            g_link->game_id = (int)id;
            ReleaseMutex(g_mutex);
        }
    }
    return 0;
}

NP_EXPORT(int) NP_RegisterWindowHandle(void *hwnd)
{
    (void)hwnd;
    return 0;
}

NP_EXPORT(int) NP_RequestData(unsigned short req)
{
    (void)req;
    return 0;
}

NP_EXPORT(int) NP_SetParameter(int a, int b)
{
    (void)a; (void)b;
    return 0;
}

NP_EXPORT(int) NP_GetParameter(int a, int b)
{
    (void)a; (void)b;
    return 0;
}

NP_EXPORT(int) NP_StartCursor(void) { return 0; }
NP_EXPORT(int) NP_StopCursor(void) { return 0; }
NP_EXPORT(int) NP_StartDataTransmission(void) { return 0; }
NP_EXPORT(int) NP_StopDataTransmission(void) { return 0; }
NP_EXPORT(int) NP_UnregisterWindowHandle(void) { return 0; }
