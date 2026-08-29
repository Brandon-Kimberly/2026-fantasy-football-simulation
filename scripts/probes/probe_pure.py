"""Arm D: pure-Python CPU/memory load with NO native extensions (no numpy, no scipy, no
matplotlib). Each round builds structures, hashes them, and checks self-consistency; any
mismatch or impossible error indicates corruption below Python. usage: probe_pure.py SECONDS"""
import faulthandler, hashlib, itertools, random, sys, time
faulthandler.enable()
seconds = float(sys.argv[1]); t0 = time.time(); rounds = 0; rng = random.Random(12345)
ref_digest = None
while time.time() - t0 < seconds:
    data = [rng.random() for _ in range(20000)]
    s = sorted(data)
    assert all(a <= b for a, b in zip(s, s[1:])), "sort order broken"
    d = {i: (x, str(x)) for i, x in enumerate(data)}
    assert len(d) == 20000 and all(d[i][0] == data[i] for i in range(0, 20000, 97)), "dict integrity broken"
    h = hashlib.sha256()
    for k in itertools.islice(itertools.permutations(range(8)), 5000):
        h.update(bytes(k))
    dig = h.hexdigest()
    if ref_digest is None: ref_digest = dig
    assert dig == ref_digest, "deterministic hash changed: %s vs %s" % (dig, ref_digest)
    rounds += 1
print("PURE PROBE OK: %d rounds in %.0fs, python %s" % (rounds, time.time() - t0, sys.version.split()[0]))
