# Manage Your Own LLM Conversations

This project is a tool to manage LLM conversations.

## Features

- Using JSON file to store conversations
- Every message has its timestamp
- Using Python to manage conversations
- Supporting Gemini form Google take out
- Supporting Gemini Voyager export
- Dynamic update conversation archive
- Never lose your conversations
- Saving at local with opening format, which is all up to you

## How to use

1. Download from [Google Takeout](https://takeout.google.com/) , locate at "My Activity" (NOT "Gemini") and chose "gemini app" (USING JSON FORMAT)
2. Export Gemini conversations using [Gemini Voyager](https://chromewebstore.google.com/detail/kjdpnimcnfinmilocccippmododhceol?utm_source=item-share-cb) one by one
3. Run the script to manage conversations
4. Have fun with exploring your conversations

## TODO

- [x] Support attachments (users input pictures, files, etc.)
- [x] Batch archiving from multiple Voyager exports
- [ ] Incremental archiving without duplication

## Future Plans
- [ ] token counter: estimate token and cost if you had used api
- [ ] A new, simple plan: put the converison ID into the original google takeout json.
- [ ] Import, export, and conversion functions for multiple formats. like [Openrouter](https://openrouter.ai/) [RikkaHub](https://github.com/rikkahub/rikkahub)
- [ ] Support Gemini Deepresearch export
- [ ] Support SQL