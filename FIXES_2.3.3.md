# Camera TTS EZVIZ Docker 2.3.3

## Root cause fixed

The single-flight implementation registered `Future.add_done_callback()` while `flight_lock` was held. Python executes a callback immediately in the registering thread when a Future is already complete. Fast cache hits could therefore re-enter the same non-reentrant lock and deadlock the HTTP request. Home Assistant then reported `Timeout on reading data from socket`.

## Changes

- callback registration moved outside the single-flight critical section; lock changed to `RLock` as a second safety guard;
- `/media` replacement stop no longer holds the global enqueue lock;
- media preparation waits for `MEDIA_PREP_TIMEOUT`, not the shorter TTS `PREP_TIMEOUT`;
- success job logging is disabled by default; error logs include camera/job/kind/stage/type/detail;
- version aligned to 2.3.3.
