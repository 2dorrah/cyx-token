#!/usr/bin/env python3
"""
CYTHANX MEGA FUSION
===================

One program with explicit cryptographic layers:

    source bytes
        -> SHA256
        -> Enigma-inspired transform
        -> BLAKE3-256 fusion root
        -> SHA256d fusion state
        -> CYTHANIZE / Scrypt commitment
        -> JOATT state
        -> optional blockchain header
        -> Scrypt
        -> SHA256d final block hash

An optional XMRig supervisor is included as an external miner interface.
The auxiliary CYTHANX layers do NOT replace or impersonate RandomX/XMRig.

No wallet, password, token, or pool credential is embedded.
"""

from __future__ import annotations
import argparse, hashlib, json, math, os, secrets, signal, subprocess, threading, time, zipfile, urllib.request, urllib.error
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

try:
    from blake3 import blake3 as _blake3
except ImportError:
    _blake3 = None


# ---------------------------------------------------------------------------
# CORE HASH LAYERS
# ---------------------------------------------------------------------------

def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()

def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()

def sha256d(data: bytes) -> bytes:
    return sha256(sha256(data))

def blake3_256(data: bytes) -> bytes:
    if _blake3 is not None:
        return _blake3(data).digest(length=32)
    return hashlib.blake2b(b"CYTHANX|BLAKE3-FALLBACK|" + data, digest_size=32).digest()

def scrypt256(data: bytes, salt: bytes | None = None) -> bytes:
    return hashlib.scrypt(
        password=data, salt=data if salt is None else salt,
        n=1024, r=1, p=1, dklen=32
    )

def pbkdf2_sha512(data: bytes, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha512", data, salt, 2048, 64)

def joaat(data: bytes) -> int:
    h = 0
    for byte in data:
        h = (h + byte) & 0xffffffff
        h = (h + ((h << 10) & 0xffffffff)) & 0xffffffff
        h ^= h >> 6
    h = (h + ((h << 3) & 0xffffffff)) & 0xffffffff
    h ^= h >> 11
    h = (h + ((h << 15) & 0xffffffff)) & 0xffffffff
    return h & 0xffffffff


# ---------------------------------------------------------------------------
# ENIGMA-INSPIRED AUXILIARY TRANSFORM
# ---------------------------------------------------------------------------

def _rotl8(x: int, n: int) -> int:
    n &= 7
    return ((x << n) | (x >> (8 - n))) & 0xff

def enigma_bytes(data: bytes, key: bytes) -> bytes:
    if len(key) < 32:
        raise ValueError("Enigma key must be at least 32 bytes")
    out = bytearray(len(data))
    for block_start in range(0, len(data), 32):
        block = data[block_start:block_start + 32]
        counter = block_start // 32
        stream = sha256(b"CYTHANX|ENIGMA|STREAM|" + key + counter.to_bytes(8, "big"))
        for j, byte in enumerate(block):
            k = stream[j]
            x = (byte + k + ((block_start + j) * 17)) & 0xff
            x ^= (key[(counter + j) % len(key)] + j * 29) & 0xff
            x = _rotl8(x, (k ^ key[j % len(key)]) & 7)
            out[block_start + j] = x
    return bytes(out)


# ---------------------------------------------------------------------------
# CYTHAN / JOATT LAYERS
# ---------------------------------------------------------------------------

VECTOR_KEY = b"CYTHAN-MAINNET-V1"
CHAIN_ID = "CYTHANX-MEGA-FUSION"

def cythan_vector() -> dict:
    vector = blake3_256(b"CYTHAN|VECTOR|" + CHAIN_ID.encode() + b"|" + VECTOR_KEY)
    return {"vector": vector, "joaat": joaat(vector)}

def cythanize(payload: bytes, previous: bytes = b"\0" * 32) -> dict:
    v = cythan_vector()
    vector, j = v["vector"], v["joaat"]
    jb = j.to_bytes(4, "big")
    state = blake3_256(b"CYTHAN|STATE|" + payload + previous + vector + jb)
    sha_state = sha256d(b"CYTHAN|SHA256D|" + state)
    scrypt_state = scrypt256(b"CYTHAN|SCRYPT|" + sha_state)
    commitment = blake3_256(
        b"CYTHAN|COMMITMENT|" + vector + jb + state + sha_state + scrypt_state
    )
    return {
        "vector": vector.hex(), "joaat": f"{j:08x}",
        "state": state.hex(), "sha256d": sha_state.hex(),
        "scrypt": scrypt_state.hex(), "commitment": commitment.hex()
    }

def joatt(payload: bytes, previous_state: bytes = b"\0" * 32) -> dict:
    b3_state = blake3_256(b"JOATT|BLAKE3|" + CHAIN_ID.encode() + previous_state + payload)
    root = sha256d(b"JOATT|ROOT|" + previous_state + b3_state + payload)
    pbkdf = pbkdf2_sha512(root, b"JOATT|PBKDF2|" + CHAIN_ID.encode())
    scrypt_state = scrypt256(pbkdf, b"JOATT|SCRYPT|" + CHAIN_ID.encode())
    mixed = blake3_256(b"JOATT|MIX|" + b3_state + root + pbkdf + scrypt_state)
    checksum = blake3_256(b"JOATT|CHECKSUM|" + mixed + root + scrypt_state)[:4]
    identifier64 = blake3_256(
        b"JOATT|ID64|" + checksum + mixed + previous_state
    )[:8]
    state256 = blake3_256(
        b"JOATT|STATE256|" + b3_state + root + pbkdf + scrypt_state +
        mixed + checksum + identifier64
    )
    return {
        "blake3": b3_state.hex(), "root_sha256d": root.hex(),
        "pbkdf2": pbkdf.hex(), "scrypt": scrypt_state.hex(),
        "mix": mixed.hex(), "checksum": checksum.hex(),
        "identifier64": identifier64.hex(), "state256": state256.hex()
    }

def transmogrify(payload: bytes, rounds: int = 3) -> bytes:
    x = payload
    for i in range(rounds):
        local_key = sha256(b"CYTHANX|LOCAL-ROTOR|" + i.to_bytes(4, "big"))
        x = enigma_bytes(x, local_key)
        x = blake3_256(b"CYTHANX|TRANSMOGRIFY|" + bytes([i]) + x)
        x = scrypt256(x, f"round-{i}".encode())
        x = sha256d(b"CYTHANX|SHA256D|ROUND|" + x)
    return x


def adaptive_lifecycle(seed: bytes, rounds: int = 3) -> dict:
    """Compatibility view of the lifecycle now owned by the organism."""
    result = organism(seed, rounds)
    return {
        "lifecycle": result["organism"]["lifecycle"],
        "final_state": result["organism"]["lifecycle_state"],
        "organism": result["organism"],
        "stages": result["lifecycle_stages"],
        "state256": result["state256"],
    }


# ---------------------------------------------------------------------------
# SOURCE FUSION
# ---------------------------------------------------------------------------

def digest_file(path: Path) -> dict:
    raw = path.read_bytes()
    return {"name": str(path), "bytes": len(raw), "sha256": sha256(raw).hex()}

def fuse_files(paths: list[Path], seed: str | None = None) -> dict:
    key = sha256(seed.encode()) if seed else secrets.token_bytes(32)
    chunks, records = [], []
    for path in paths:
        if not path.exists():
            records.append({"name": str(path), "status": "missing"})
            continue
        raw = path.read_bytes()
        transformed = enigma_bytes(raw, key)
        actual = sha256(raw)
        records.append({
            "name": str(path), "status": "ok", "bytes": len(raw),
            "sha256": actual.hex(), "enigma_sha256": sha256(transformed).hex()
        })
        chunks.append(path.name.encode() + b"\0" + len(raw).to_bytes(8, "big") + actual + transformed)
    envelope = b"CYTHANX|MEGA-FUSION|V2|" + b"".join(chunks)
    root = blake3_256(envelope)
    state = sha256d(b"CYTHANX|FUSION-SHA256D|" + root)
    commitment = blake3_256(b"CYTHANX|FUSION-COMMIT|" + root + state + key)
    return {
        "version": 2, "source_count": len(paths), "key_hex": key.hex(),
        "records": records, "fusion_root": root.hex(),
        "fusion_sha256d": state.hex(), "fusion_commitment": commitment.hex()
    }


# ---------------------------------------------------------------------------
# LAYERED BLOCK ENGINE
# ---------------------------------------------------------------------------

TARGET = int("00000fffffffffffffffffffffffffffffffffffffffffffffffffffffffffff", 16)

# CYTHANX canonical 80-byte block header.
#
# Serialization is consensus-critical and deliberately independent of JSON:
#   version       uint32 LE   4 bytes
#   prev_hash     raw digest   32 bytes
#   merkle_root   raw digest   32 bytes
#   timestamp     uint32 LE   4 bytes
#   nBits         uint32 LE   4 bytes
#   nonce         uint32 LE   4 bytes
#                               --------
#                               80 bytes
#
# Hash fields are serialized as their 32-byte digest in natural byte order
# (the same order produced by hashlib.digest()). They are NOT reversed for
# display/serialization. Integer fields are unsigned little-endian.
# The payload is UTF-8 encoded and forms a single-leaf Merkle tree: its leaf
# hash is SHA256d(payload_bytes), which is therefore the Merkle root.

HEADER_SIZE = 80
HEADER_VERSION = 4

@dataclass
class Block:
    height: int
    timestamp: int
    previous_hash: str
    payload: str
    nonce: int = 0
    nbits: int = 0x1F0FFFFF
    version: int = HEADER_VERSION
    hash: str = ""

    def _hash32(self, value: str, field: str) -> bytes:
        try:
            raw = bytes.fromhex(value)
        except ValueError as exc:
            raise ValueError(f"{field} must be hexadecimal") from exc
        if len(raw) != 32:
            raise ValueError(f"{field} must contain exactly 32 bytes / 64 hex characters")
        return raw

    def payload_bytes(self) -> bytes:
        return self.payload.encode("utf-8")

    def merkle_root(self) -> bytes:
        # Single-leaf Merkle tree. Multi-transaction Merkle construction can
        # replace this later without changing the 80-byte header layout.
        return sha256d(self.payload_bytes())

    def header_bytes(self) -> bytes:
        if not 0 <= self.version <= 0xFFFFFFFF:
            raise ValueError("version must fit uint32")
        if not 0 <= self.timestamp <= 0xFFFFFFFF:
            raise ValueError("timestamp must fit uint32")
        if not 0 <= self.nbits <= 0xFFFFFFFF:
            raise ValueError("nBits must fit uint32")
        if not 0 <= self.nonce <= 0xFFFFFFFF:
            raise ValueError("nonce must fit uint32")

        header = b"".join((
            self.version.to_bytes(4, "little"),
            self._hash32(self.previous_hash, "previous_hash"),
            self.merkle_root(),
            self.timestamp.to_bytes(4, "little"),
            self.nbits.to_bytes(4, "little"),
            self.nonce.to_bytes(4, "little"),
        ))
        if len(header) != HEADER_SIZE:
            raise AssertionError(f"CYTHANX header must be {HEADER_SIZE} bytes")
        return header

    def header(self) -> dict:
        # Human-readable representation; NEVER used for consensus hashing.
        return {
            "version": self.version,
            "height": self.height,
            "previous_hash": self.previous_hash,
            "merkle_root": self.merkle_root().hex(),
            "timestamp": self.timestamp,
            "nBits": f"{self.nbits:08x}",
            "nonce": self.nonce,
            "header_hex": self.header_bytes().hex(),
        }

    def layered_hash(self) -> bytes:
        header = self.header_bytes()
        # Layer order is deliberately explicit and consensus-critical.
        stage1 = blake3_256(b"CYTHANX|HEADER|" + header)
        stage2 = scrypt256(stage1, salt=header)
        stage3 = sha256d(b"CYTHANX|POW|SHA256D|" + stage2)
        return stage3

    def mine(self, target: int = TARGET, max_nonce: int | None = None) -> bool:
        while max_nonce is None or self.nonce <= max_nonce:
            digest = self.layered_hash()
            if int.from_bytes(digest, "big") <= target:
                self.hash = digest.hex()
                return True
            self.nonce += 1
        return False


# ---------------------------------------------------------------------------
# ORGANISM/TELEMETRY STATE
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# NATIVE CYTHANX TOKENOMICS
# ---------------------------------------------------------------------------

@dataclass
class TokenomicsState:
    """Native integer accounting state carried by each organism."""
    symbol: str = "CYTHANX"
    decimals: int = 8
    max_supply: int = 21_000_000 * 10**8
    epoch: int = 0
    base_emission: int = 50 * 10**8
    organism_yield: int = 0
    treasury: int = 0
    reserve: int = 0
    burned: int = 0
    circulating: int = 0
    cumulative_emission: int = 0
    allocation_root: str = ""

    def allocate(self, lifecycle_state: bytes, generation: int) -> dict:
        """Commit one lifecycle generation into bounded token accounting."""
        if not isinstance(lifecycle_state, bytes) or len(lifecycle_state) != 32:
            raise ValueError("lifecycle_state must be exactly 32 bytes")
        generation = max(1, int(generation))
        self.epoch = (generation - 1) // 210_000
        epoch_emission = max(1, self.base_emission >> min(self.epoch, 63))
        remaining = max(0, self.max_supply - self.cumulative_emission)
        emission = min(epoch_emission, remaining)
        score = int.from_bytes(blake3_256(
            b"CYTHANX|CONTRIBUTION|" + lifecycle_state
        )[:8], "big")

        # Fixed accounting split: 80% organism yield, 10% treasury, 10% reserve.
        yield_share = (emission * 80) // 100
        treasury_share = (emission * 10) // 100
        reserve_share = emission - yield_share - treasury_share
        self.organism_yield += yield_share
        self.treasury += treasury_share
        self.reserve += reserve_share
        self.circulating += yield_share
        self.cumulative_emission += emission

        # Accounting invariant: every emitted unit belongs to exactly one bucket.
        if self.organism_yield + self.treasury + self.reserve + self.burned != self.cumulative_emission:
            raise AssertionError("CYTHANX tokenomics accounting invariant violated")
        if self.cumulative_emission > self.max_supply:
            raise AssertionError("CYTHANX max supply exceeded")

        previous = bytes.fromhex(self.allocation_root) if self.allocation_root else b"CYTHANX|ALLOCATION|GENESIS"
        self.allocation_root = blake3_256(
            b"CYTHANX|ALLOCATION|" + previous + lifecycle_state +
            generation.to_bytes(8, "big") + emission.to_bytes(16, "big") +
            score.to_bytes(8, "big")
        ).hex()
        return {
            "generation": generation,
            "epoch": self.epoch,
            "contribution_score": score,
            "emission": emission,
            "organism_yield": yield_share,
            "treasury": treasury_share,
            "reserve": reserve_share,
            "cumulative_emission": self.cumulative_emission,
            "allocation_root": self.allocation_root,
        }


@dataclass
class OrganismState:
    energy: float = 1.0
    entropy: float = 0.0
    health: float = 1.0
    generation: int = 0
    lifecycle: str = "COIL->METAMORPHOSIS->YIELD->MUTATE->MOLD->RANDOM->SHA256->ADAPT"
    lifecycle_state: str = ""
    tokenomics: TokenomicsState = field(default_factory=TokenomicsState)

    def adapt(self, signal_value: bytes) -> None:
        x = int.from_bytes(blake3_256(signal_value)[:8], "big") / 2**64
        self.entropy = -math.log2(max(x, 2**-64)) / 64.0
        self.energy = max(0.0, min(2.0, self.energy * (1.0 + 0.02 + 0.08*x)))
        self.health = max(0.0, min(1.0, 0.97*self.health + 0.03*(1.0-abs(x-0.5))))
        self.generation += 1

    def lifecycle_step(self, seed: bytes, round_index: int) -> dict:
        """Core CYTHANX organism trait: evolve one complete lifecycle round."""
        i = int(round_index)
        coil = sha256(b"CYTHANX|COIL|" + seed + i.to_bytes(4, "big"))
        metamorphosis = transmogrify(
            b"CYTHANX|METAMORPHOSIS|" + coil + i.to_bytes(4, "big"),
            2 + (i % 3),
        )
        yielded = blake3_256(b"CYTHANX|YIELD|" + metamorphosis + coil)
        mutation = sha256d(
            b"CYTHANX|MUTATE|" + yielded + coil + i.to_bytes(4, "big")
        )
        mold = blake3_256(b"CYTHANX|MOLD|" + mutation + yielded)
        random_bytes = secrets.token_bytes(32)
        random_stage = sha256(b"CYTHANX|RANDOM|" + random_bytes + mold)
        sha_stage = sha256(
            b"CYTHANX|ADAPT-SHA256|" + random_stage + mold + mutation
        )
        self.adapt(sha_stage)
        self.lifecycle_state = blake3_256(
            b"CYTHANX|LIFECYCLE-STATE|" + sha_stage + mutation + mold
        ).hex()
        tokenomics = self.tokenomics.allocate(
            bytes.fromhex(self.lifecycle_state), self.generation
        )
        return {
            "round": i + 1,
            "coil": coil.hex(),
            "metamorphosis": metamorphosis.hex(),
            "yield": yielded.hex(),
            "mutation": mutation.hex(),
            "mold": mold.hex(),
            "random": random_bytes.hex(),
            "sha256": sha_stage.hex(),
            "state": self.lifecycle_state,
            "generation": self.generation,
            "tokenomics": tokenomics,
        }

def organism(seed: bytes, rounds: int = 3) -> dict:
    rounds = max(1, int(rounds))
    state, previous = blake3_256(b"CYTHANX|GENESIS|" + seed), b""
    org = OrganismState()
    stages = []
    for i in range(rounds):
        stage = org.lifecycle_step(seed + state + previous, i)
        stages.append(stage)
        payload = enigma_bytes(
            state + previous + bytes.fromhex(stage["sha256"]),
            sha256(seed + i.to_bytes(4, "big")),
        )
        jo = joatt(payload, previous)
        vec = cythanize(bytes.fromhex(jo["state256"]), previous)
        transformed = transmogrify(bytes.fromhex(vec["commitment"]), 3 + i % 3)
        aux = blake3_256(
            b"CYTHANX|AUX|" + transformed + bytes.fromhex(stage["state"])
        )[:16]
        state = blake3_256(
            b"CYTHANX|STATE|" + state + aux + i.to_bytes(8, "big")
        )
        previous = bytes.fromhex(jo["state256"])
    return {
        "state256": state.hex(),
        "organism": asdict(org),
        "tokenomics": asdict(org.tokenomics),
        "lifecycle_stages": stages,
    }


# ---------------------------------------------------------------------------
# XMRIG MEGA-FUSION SUPERVISOR
# ---------------------------------------------------------------------------
# XMRig remains an external mining engine. CYTHANX owns the orchestration,
# telemetry, lifecycle, fusion/organism state, and restart policy. The actual
# proof-of-work algorithm is whatever XMRig is configured to run (rx/0 by
# default); this module does not claim that XMRig natively implements the
# CYTHANX layered hash.

STOP = threading.Event()
PROCESS: subprocess.Popen | None = None


def _api_json(args: argparse.Namespace, path: str) -> dict | None:
    """Read a local XMRig HTTP API endpoint without external dependencies."""
    url = f"http://127.0.0.1:{args.api_port}{path}"
    req = urllib.request.Request(url)
    if getattr(args, "api_token", None):
        req.add_header("Authorization", f"Bearer {args.api_token}")
    try:
        with urllib.request.urlopen(req, timeout=2.0) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None


def xmrig_command(args: argparse.Namespace) -> list[str]:
    threads = args.threads or max(1, os.cpu_count() or 1)
    if not args.user:
        raise ValueError("--user is required for XMRig mode")

    cmd = [
        args.xmrig_path,
        "--algo", args.algo,
        "--url", args.pool,
        "--user", args.user,
        "--pass", args.password,
        "--threads", str(threads),
        "--http-host", "127.0.0.1",
        "--http-port", str(args.api_port),
        "--print-time", str(args.print_time),
    ]
    if getattr(args, "api_token", None):
        cmd += ["--http-access-token", args.api_token]
    if getattr(args, "rig_id", None):
        cmd += ["--rig-id", args.rig_id]
    if getattr(args, "keepalive", False):
        cmd += ["--keepalive"]
    if getattr(args, "tls", False):
        cmd += ["--tls"]
    return cmd


def write_xmrig_config(args: argparse.Namespace, path: Path) -> Path:
    """Emit a portable XMRig config matching the supervisor settings."""
    threads = args.threads or max(1, os.cpu_count() or 1)
    config = {
        "autosave": True,
        "background": False,
        "colors": True,
        "print-time": args.print_time,
        "health-print-time": args.print_time,
        "donate-level": 1,
        "http": {
            "enabled": True,
            "host": "127.0.0.1",
            "port": args.api_port,
            "access-token": args.api_token or None,
            "restricted": True,
        },
        "pools": [{
            "algo": args.algo,
            "url": args.pool,
            "user": args.user or "SET_YOUR_WALLET_OR_POOL_USER",
            "pass": args.password,
            "rig-id": args.rig_id or None,
            "keepalive": bool(args.keepalive),
            "tls": bool(args.tls),
            "enabled": True,
        }],
        "cpu": {
            "enabled": True,
            "max-threads-hint": 100,
            "threads": threads,
        },
    }
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return path


def xmrig_status(args: argparse.Namespace) -> dict:
    summary = _api_json(args, "/1/summary") or {}
    hashrate = summary.get("hashrate", {}) if isinstance(summary, dict) else {}
    results = summary.get("results", {}) if isinstance(summary, dict) else {}
    return {
        "worker": getattr(args, "rig_id", None) or getattr(args, "user", None),
        "algo": args.algo,
        "pool": args.pool,
        "uptime": summary.get("uptime"),
        "hashrate": hashrate,
        "accepted": results.get("shares_good", results.get("accepted")),
        "rejected": results.get("shares_total", results.get("rejected")),
        "raw": summary,
    }


def stop_xmrig() -> None:
    global PROCESS
    if PROCESS is None or PROCESS.poll() is not None:
        PROCESS = None
        return
    try:
        PROCESS.terminate()
        PROCESS.wait(timeout=10)
    except Exception:
        try:
            PROCESS.kill()
        except Exception:
            pass
    PROCESS = None


def _xmrig_banner(args: argparse.Namespace, generation: int) -> None:
    print("=" * 72)
    print(" CYTHANX MEGA FUSION // XMRIG ORGANISM ENGINE")
    print(f" ALGORITHM : {args.algo}")
    print(f" POOL      : {args.pool}")
    print(f" WORKER    : {args.rig_id or args.user}")
    print(f" GENERATION: {generation}")
    print(" CYTHANX   : FUSION -> JOATT -> ORGANISM -> MINER -> TELEMETRY")
    print("=" * 72)


def run_xmrig(args: argparse.Namespace) -> int:
    global PROCESS
    STOP.clear()

    def shutdown(signum, frame):
        STOP.set()
        stop_xmrig()

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    # Derive a deterministic organism seed for this supervisor session.
    session_seed = sha256((args.seed or "CYTHANX-MEGA-FUSION").encode())
    generation = organism(session_seed, rounds=max(1, args.organism_rounds))["organism"]["generation"]
    _xmrig_banner(args, generation)

    if getattr(args, "write_config", None):
        config_path = write_xmrig_config(args, Path(args.write_config))
        print(f"[CYTHANX] XMRig config written: {config_path}")

    restart_count = 0
    while not STOP.is_set():
        try:
            cmd = xmrig_command(args)
            print(f"[CYTHANX/XMRIG] starting (restart={restart_count})")
            PROCESS = subprocess.Popen(cmd)
            last_status = 0.0

            while not STOP.is_set():
                if PROCESS.poll() is not None:
                    print(f"[CYTHANX/XMRIG] process exited with code {PROCESS.returncode}")
                    break

                now = time.monotonic()
                if now - last_status >= args.status_interval:
                    status = xmrig_status(args)
                    # Feed miner telemetry into the organism without changing
                    # XMRig's own PoW semantics.
                    signal_bytes = canonical({
                        "generation": generation,
                        "status": status,
                    })
                    org = organism(sha256(signal_bytes), rounds=1)
                    print(json.dumps({
                        "event": "miner_telemetry",
                        "generation": generation,
                        "organism": org["organism"],
                        "miner": {
                            "uptime": status.get("uptime"),
                            "hashrate": status.get("hashrate"),
                            "accepted": status.get("accepted"),
                            "rejected": status.get("rejected"),
                        },
                    }, separators=(",", ":")))
                    generation += 1
                    last_status = now
                time.sleep(0.5)

            if STOP.is_set():
                break
            restart_count += 1
            time.sleep(max(1, args.restart_delay))
        except FileNotFoundError:
            print(f"[CYTHANX/XMRIG] XMRig executable not found: {args.xmrig_path}")
            return 2
        except Exception as exc:
            print(f"[CYTHANX/XMRIG] supervisor error: {exc}")
            if STOP.is_set():
                break
            time.sleep(max(1, args.restart_delay))

    stop_xmrig()
    print("[CYTHANX/XMRIG] supervisor stopped cleanly")
    return 0


# ---------------------------------------------------------------------------
# CLI / SELF TEST
# ---------------------------------------------------------------------------

def self_test() -> dict:
    sample = b"CYTHANX|SELF-TEST"
    d = sha256d(sample)
    c = cythanize(sample)
    j = joatt(sample)
    o = organism(d, 2)
    block = Block(0, 0, "00"*32, c["commitment"])
    # Avoid an expensive proof-of-work test: verify deterministic 32-byte output.
    header = block.header_bytes()
    digest = block.layered_hash()
    assert len(header) == HEADER_SIZE == 80
    assert len(d) == len(digest) == 32
    assert len(bytes.fromhex(j["state256"])) == 32
    assert o["organism"]["generation"] == 2
    return {
        "ok": True, "sha256d": d.hex(),
        "cythan_commitment": c["commitment"],
        "joatt_state256": j["state256"],
        "layered_block_hash": digest.hex(),
        "header_size": len(header),
        "blake3_backend": "blake3" if _blake3 else "blake2b-fallback"
    }

def parse_args():
    p = argparse.ArgumentParser(description="CYTHANX one-program layered fusion engine")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--fuse", nargs="*", type=Path)
    p.add_argument("--seed")
    p.add_argument("--organism", action="store_true")
    p.add_argument("--adaptive-lifecycle", action="store_true", help="run the adaptive lifecycle")
    p.add_argument("--mine-block", action="store_true")
    p.add_argument("--xmrig", action="store_true", help="run the external XMRig supervisor")
    p.add_argument("--auto", action="store_true", help="Termux-style self-test then start XMRig")
    p.add_argument("--xmrig-path", default=os.environ.get("CYTHANX_XMRIG", os.environ.get("XMRIG_PATH", "xmrig")))
    p.add_argument("--pool", default=os.environ.get("CYTHANX_POOL", os.environ.get("XMRIG_POOL", "xmrig.nanswap.com:3333")))
    p.add_argument("--user", default=os.environ.get("CYTHANX_USER", os.environ.get("XMRIG_USER")))
    p.add_argument("--password", default=os.environ.get("CYTHANX_PASS", os.environ.get("XMRIG_PASS", "x")))
    p.add_argument("--algo", default=os.environ.get("CYTHANX_ALGO", os.environ.get("XMRIG_ALGO", "rx/0")))
    p.add_argument("--threads", type=int)
    p.add_argument("--api-port", type=int, default=16000)
    p.add_argument("--print-time", type=int, default=60)
    p.add_argument("--restart-delay", type=int, default=5)
    p.add_argument("--status-interval", type=int, default=15, help="XMRig telemetry interval in seconds")
    p.add_argument("--api-token", default=os.environ.get("CYTHANX_XMRIG_API_TOKEN"), help="optional XMRig HTTP API bearer token")
    p.add_argument("--rig-id", default=os.environ.get("CYTHANX_RIG_ID"), help="XMRig rig identifier")
    p.add_argument("--keepalive", action="store_true", help="request pool keepalive")
    p.add_argument("--tls", action="store_true", help="enable XMRig pool TLS")
    p.add_argument("--write-config", help="write an XMRig JSON config and continue")
    p.add_argument("--organism-rounds", type=int, default=3, help="CYTHANX organism rounds for supervisor telemetry")
    return p.parse_args()

def main() -> int:
    args = parse_args()
    if args.self_test:
        print(json.dumps(self_test(), indent=2))
        return 0

    if args.auto:
        # Equivalent to the supplied Termux launcher: validate first, then
        # hand off to the external XMRig supervisor.
        print("[CYTHANX] Automatic supervisor mode")
        print(json.dumps(self_test(), indent=2))
        args.xmrig = True

    if args.fuse is not None:
        result = fuse_files(args.fuse, args.seed)
        print(json.dumps(result, indent=2))
        seed = bytes.fromhex(result["fusion_commitment"])
    else:
        seed = sha256((args.seed or "CYTHANX").encode())

    if args.organism:
        print(json.dumps(organism(seed), indent=2))

    if args.adaptive_lifecycle:
        print(json.dumps(adaptive_lifecycle(seed, args.organism_rounds), indent=2))

    if args.mine_block:
        payload = cythanize(seed)["commitment"]
        block = Block(0, int(time.time()), "00"*32, payload)
        if block.mine():
            print(json.dumps(asdict(block), indent=2))
        else:
            print("Block mining stopped.")
            return 1

    if args.xmrig:
        return run_xmrig(args)

    if args.fuse is None and not args.organism and not args.adaptive_lifecycle and not args.mine_block:
        print(json.dumps(self_test(), indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
