#!/usr/bin/env bash
# Nightly backup: auth+encounters SQLite DB, ASR recording segments, RAG
# chroma_store. Writes to a separate physical disk (/data) so a single-disk
# failure on the app disk doesn't take out both the primary copy and the
# backup. This is NOT offsite/cloud backup -- it does not protect against
# fire/theft/site loss. Provisioning a true offsite target needs cloud
# credentials this script does not have.
#
# SECURITY (Step 9): every backup artifact is ENCRYPTED AT REST before it is
# written -- clinical PHI (user DB, ASR audio, RAG chroma) must not sit in
# plaintext on /data. gpg AES256 symmetric encryption with the passphrase from
# /etc/dreamcision/backup-passphrase (eissa-readable 640, root group). The
# plaintext artifact is removed only after the .gpg is verified.
#
#   Decrypt to restore:
#     gpg --batch --decrypt --passphrase-file /etc/dreamcision/backup-passphrase  \
#         -o <out>  <backup>/<artifact>.gpg
#
# NOTE: the passphrase lives on this same box (single copy). It protects the
# archived PHI against exfiltration/casual read at rest, not against loss of
# the key -- back the passphrase up to a trusted root-held store.
set -euo pipefail

REPO_ROOT="/opt/dreamcision"
CNG_DATA="$REPO_ROOT/Clinical-Note-Generator/data"
RAG_CHROMA="$REPO_ROOT/RAG/chroma_store"

BACKUP_ROOT="/data/backups/dreamcision"
STAMP="$(date +%Y-%m-%d_%H%M%S)"
DEST="$BACKUP_ROOT/$STAMP"
RETENTION_DAYS=14
PASSPHRASE_FILE="/etc/dreamcision/backup-passphrase"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

fail() {
    log "FAILED: $*"
    rm -rf "$DEST"
    exit 1
}

# Restrictive umask: nothing written by this script may be world-readable.
umask 077

if [ ! -r "$PASSPHRASE_FILE" ]; then
    fail "backup passphrase not readable at $PASSPHRASE_FILE (run setup as root)"
fi

mkdir -p "$DEST"
log "Starting backup to $DEST"

# gpg-encrypt a file at rest, removing plaintext after the ciphertext is
# produced and verified (so a failed encryption never loses the plaintext).
encrypt_at_rest() {
    local plain="$1"
    [ -f "$plain" ] || { log "encrypt_at_rest: $plain missing, skipping"; return; }
    gpg --batch --yes --quiet \
        --symmetric --cipher-algo AES256 \
        --passphrase-file "$PASSPHRASE_FILE" \
        --output "$plain.gpg" "$plain" \
        || fail "gpg encryption failed for $plain"
    # Verify the ciphertext is decodable back to the expected size before
    # deleting the plaintext.
    local dec_bytes
    dec_bytes="$(gpg --batch --quiet --decrypt --passphrase-file "$PASSPHRASE_FILE" \
        --output - "$plain.gpg" | wc -c)"
    local plain_bytes
    plain_bytes="$(wc -c < "$plain")"
    if [ "$dec_bytes" != "$plain_bytes" ]; then
        fail "integrity check failed for $plain ($dec_bytes != $plain_bytes)"
    fi
    rm -f "$plain"
    log "encrypted + verified: $(basename "$plain") -> $(basename "$plain.gpg") ($(du -h "$plain.gpg" | cut -f1))"
}

# --- 1. SQLite DB (auth + encounters): use the online backup API, not cp,
# so a consistent snapshot is taken even while the app is writing under WAL.
DB_SRC="$CNG_DATA/user_data.sqlite"
DB_DEST="$DEST/user_data.sqlite"
if [ ! -f "$DB_SRC" ]; then
    fail "SQLite DB not found at $DB_SRC"
fi
sqlite3 "$DB_SRC" ".backup '$DB_DEST'" || fail "sqlite3 .backup failed"
INTEGRITY="$(sqlite3 "$DB_DEST" "PRAGMA integrity_check;")"
if [ "$INTEGRITY" != "ok" ]; then
    fail "backup DB failed integrity_check: $INTEGRITY"
fi
DB_SIZE="$(du -h "$DB_DEST" | cut -f1)"
log "DB backup ok ($DB_SIZE, integrity_check passed)"
encrypt_at_rest "$DB_DEST"

# --- 2. ASR recording segments (audio).
AUDIO_SRC="$CNG_DATA/asr_recording_segments"
if [ -d "$AUDIO_SRC" ]; then
    tar -czf "$DEST/asr_recording_segments.tar.gz" -C "$CNG_DATA" asr_recording_segments \
        || fail "asr_recording_segments tar failed"
    AUDIO_SIZE="$(du -h "$DEST/asr_recording_segments.tar.gz" | cut -f1)"
    log "Audio backup ok ($AUDIO_SIZE)"
    encrypt_at_rest "$DEST/asr_recording_segments.tar.gz"
else
    log "WARNING: $AUDIO_SRC not found, skipping (nothing to back up yet)"
fi

# --- 3. RAG chroma_store. Regenerable from RAG/scripts/weekly_run.sh, so a
# plain copy (not an online-backup API) is an acceptable consistency
# tradeoff here -- worst case a rare mid-write snapshot, refreshed by the
# next weekly rebuild regardless.
if [ -d "$RAG_CHROMA" ]; then
    tar -czf "$DEST/rag_chroma_store.tar.gz" -C "$REPO_ROOT/RAG" chroma_store \
        || fail "rag_chroma_store tar failed"
    RAG_SIZE="$(du -h "$DEST/rag_chroma_store.tar.gz" | cut -f1)"
    log "RAG chroma_store backup ok ($RAG_SIZE)"
    encrypt_at_rest "$DEST/rag_chroma_store.tar.gz"
else
    log "WARNING: $RAG_CHROMA not found, skipping"
fi

TOTAL_SIZE="$(du -sh "$DEST" | cut -f1)"
log "Backup complete: $DEST ($TOTAL_SIZE)"

# --- Retention: prune backups older than RETENTION_DAYS.
find "$BACKUP_ROOT" -maxdepth 1 -mindepth 1 -type d -mtime "+$RETENTION_DAYS" | while read -r old; do
    log "Pruning old backup: $old"
    rm -rf "$old"
done

log "Done."
