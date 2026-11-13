# Design

Use the stdlib email multipart parser after bounding the full request Content-Length to256MiB, including multipart overhead. Reject duplicate/invalid/missing lengths, transfer encoding, malformed multipart, empty files and duplicate image parts. The limit is a request-size bound, not a process-memory guarantee; parsing retains bounded request bytes in memory. Write the selected image into a temporary directory, remove it afterwards, and generate UTF-8 output. Bind127.0.0.1 and print the actual bound port. Tests start real ephemeral local HTTP servers and a module subprocess.
