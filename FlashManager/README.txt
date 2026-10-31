FlashManager
Red Dead Redemption 2 — story-mode mod setup

FlashManager prepares a PC copy of Red Dead Redemption 2 for story-mode mods,
then sorts mods you drop on it into the right game folders.


Start
-----
1. Install Python 3 from https://www.python.org/downloads/
   On the installer, check "Add python.exe to PATH".
2. Double-click "Start FlashManager.bat".
3. Leave the black window open. The page opens in your browser.
4. Close that window when you are done. Closing the browser alone does not stop it.

The FlashManager folder can sit anywhere. It can also sit inside the game folder.
It looks for Steam, Epic, and Rockstar installs. If yours is missing, paste the
folder that contains RDR2.exe and press Enter, or click Browse.

Rockstar puts the game in Program Files. Windows blocks new folders there, including
scripts, unless FlashManager is running as administrator. When the page says access
is denied, click Run as administrator and allow the prompt, then run Prep again.

The page has two tabs. Prep installs the story-mode tools. Modify is where you
add and remove mods, and where story saves are dropped.


Prep
----
Prep downloads the current story-mode tools from each author's own site:

  Script Hook RDR2          Alexander Blade, dev-c.com
  Ultimate ASI Loader       ThirteenAG, GitHub
  ScriptHookRDR2 .NET V2    Halen84, GitHub
  Lenny's Mod Loader        rdr2mods.com

Native Trainer is off unless you tick it. It is the sample F4 trainer that
ships with Script Hook, and it fights with other trainers.

Close the game before Prep or a drop. The game locks its files while it is open.

Epic and the Rockstar launcher sometimes ignore dinput8.dll. If the game starts
and mods do nothing, set the ASI loader to version.dll and run Prep again.
Do not leave both dinput8.dll and version.dll in the game folder. Two loaders
load every plugin twice and can crash the game.

Tick "Reinstall anyway" when you want Prep to replace a tool that is already there.

C# scripts need .NET Framework 4.8 and the Visual C++ 2015-2022 x64 runtime.
The page says so when either one is missing.


Lenny's Mod Loader
------------------
Lenny's site often asks the browser to prove it is a person, so FlashManager
cannot always download that zip by itself. When that happens it opens the
official page. Download the zip whose name starts with lml_rdr_beta. FlashManager
installs it when the file finishes landing in your Downloads folder.

You can also drop that zip on the pad. If an older copy is already in Downloads,
use "Install the zip in Downloads". "Skip Lenny's" continues without it.

Drop the full lml_rdr zip, not a loose vfs.asi. Mod Manager is copied to
ModManager and is not launched.

The same steps are on the page, under How to use.

Popular mods
------------
The page lists a few well-known story-mode mods. Get downloads the latest
release from that author's own GitHub and copies it into the game folder.
Update fetches that release again. Remove takes the mod back out and keeps
the previous copy in _flashmanager_backup. Run Prep first. Chaos Mod and
RedLua both open with F7. None of these are for Red Dead Online. Mods that
only live behind a login on another site are not included.

Mods you dropped are listed under In the game. Remove, then Remove now, takes
that folder or plugin out. Script Hook, the ASI loader, Lenny's Mod Loader,
and the .NET plugin stay installed. Those are removed only by deleting them
in the game folder.


Story saves
-----------
Story saves do not live in the game folder. They live in:

  Documents\Rockstar Games\Red Dead Redemption 2\Profiles\<your id>\

Drop a file named SRDR30000 through SRDR30015, or a zip of those files, on
the pad. Pick the profile first when more than one is listed. SRDR30015 is
the autosave. SRDR30000 is manual slot 1, SRDR30001 is slot 2, and so on.
A matching .bak file can be dropped with it.

Launch story mode once and make any save if no profile is listed yet.
The file you replace is copied to _flashmanager_save_backup, next to the
Profiles folder. If the game puts the old save back, turn off Rockstar cloud
sync for Red Dead Redemption 2 and drop the save again.


Dropping mods
-------------
Drop a zip, a folder, or loose files on the pad. You can also use Choose files
or Choose a folder.

  install.xml mod          goes in lml, under the mod's name
  .asi plugin              sits next to RDR2.exe
  C# .dll or .cs script    goes in scripts
  .ini next to a plugin    stays beside that plugin

A folder that is already named lml or scripts is merged into the matching
game folder.

These are set aside in _flashmanager_review ("Needs a look" on the page):

  loose .ytd, .ydr, and other game assets with no install.xml
  OpenIV packages (.oiv or assembly.xml)
  .rpf archives
  .exe programs

FlashManager does not run dropped programs, and it does not invent an
install.xml. Asset mods need one or Lenny's Mod Loader will ignore them.

A drop that contains RDR2.exe is refused. Drop the mod, not the whole game.
Game files such as RDR2.exe, PlayRDR2.exe, and the store DLLs are never replaced.

When FlashManager replaces a file, the previous copy goes in
_flashmanager_backup. You can delete a backup once the game is running the
way you want.


Story mode only
---------------
Use this on story mode. Script Hook closes the game if you enter Red Dead
Online, and playing Online with these files installed can cost you the account.
Take the mod files out before you play Online.

Launch story mode from Steam, Epic, or the Rockstar Launcher after Prep.


If a script does not load
-------------------------
Rockstar updates the game from time to time. When the game version and Script
Hook no longer match, run Prep again after Script Hook is updated on dev-c.com.
Older game builds sometimes need an older Lenny's Mod Loader beta than the one
on the front of the rdr2mods page.
