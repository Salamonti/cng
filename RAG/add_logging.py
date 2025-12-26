# C:\RAG\add_logging.py
#!/usr/bin/env python3
"""Add error logging to ASR endpoint"""

with open('/home/islameissa/projects/Clinical-Note-Generator/server/routes/asr.py', 'r') as f:
    content = f.read()

# Add better error logging
old = """    except Exception as e:
        return PlainTextResponse(str(e), status_code=503)"""

new = """    except Exception as e:
        print(f"❌ Transcription error: {e}")
        import traceback
        traceback.print_exc()
        return PlainTextResponse(str(e), status_code=503)"""

content = content.replace(old, new)

with open('/home/islameissa/projects/Clinical-Note-Generator/server/routes/asr.py', 'w') as f:
    f.write(content)

print("✅ Added error logging!")
