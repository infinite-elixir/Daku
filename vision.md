# Daku - Main functionality needed
A app that is inspired by Tsearch (/home/abhi/Downloads/Opencode/Tsearch) that uses imdb (fallback to cinemata) + torrentio to do the exact stuff.
But this is a app, can be a windows app or a android app, need something that is cross compatible and can easily be ported over to other without any issue. It will do the following:
1. Have a search functionality, add to library (stored data in sql). According to Movies/Anime and stuff. 
2. In that show have, torrent searching, using torrentio (for now, can expand later to better alternatives) and then save that torrent/magnet as a recent option. 
3. Have a download functionality, it directly uses aria2c to download, and shows progress in the web app itself. 
4. The downloaded file, can be streamed over the wifi network using `--host 0.0.0.0`, (This needs some problem assement as to figuring out if every kind of media is streamble or not, from what i remember some type fo media is hard to stream over simple stuff, needs research),
5. This is the tricky part, it also should have a telegram client attached to it, either a bot or an actual account, that can download media and organise media. 

## Telegram Client part

It could work like this:
1. We could have a central channel/group that is the storage part and the organising part of it part of it.
2. It could store ID and some stuff like that, which then it can identify, directly from it.
3. It could then fetch stuff in order, and fetch the file metdata and stuff, and have a download button or stuff, and then with a click here it can download, and get converted to media living on the device. 
4. It could also have streamble fucntions, can be referenced or copied from the repo telegram-stremio

This part needs feasibiltiy check on what to use, what speeds to expect, and which stuff is better, to do. 



