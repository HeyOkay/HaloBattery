# Public RFC3161 fixture

`timestamped-cms.b64` contains only the public Authenticode CMS signature from
the HaloBattery TEST executable produced by Actions run `45613771991`, attempt 1,
commit `93364ba21c038ea719c9b2ede76277c9da32fe3e`.
It includes public certificates and a signed RFC3161 token, no executable code or
private key. Decoded SHA256:
`7141765A8B1DFE52ABAC5DCC7DF176236A4E32D3D4D5A68978872AB09C4708DD`.

The fixture isolates timestamp parsing/binding. It does not simulate a trusted
production certificate or validate an executable's file digest. Tests create a
different signer in memory to prove this token cannot timestamp another signature.
No certificate is installed into a trust store and no network is used.
