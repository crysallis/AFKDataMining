import re
import time

from device import screenshot, scroll_down, scroll_to_top, screen_changed, tap, back
from parser import parse_member_anchors
from db import members_needing_ingame_id, set_ingame_id, make_roster_resolver
from ocr import ocr_image as _ocr

# "User ID: 18939236" in the profile popup. \D* skips the label/colon so the
# captured digits are the ID, not the "Server: S282" number elsewhere on screen.
USER_ID_RE = re.compile(r"User\s*ID\D*(\d+)", re.IGNORECASE)

AVATAR_X = 120          # far-left profile square (rank slot in other lists)
AVATAR_Y_OFFSET = 55    # avatar center sits ~55px below the name/timestamp row
FRAMES_PER_STOP = 2     # OCR this many spread frames per scroll stop (like the
                        # mode scans) so a card the OCR flubs in one frame — a
                        # mangled name or unread timestamp — is caught in the other


def _read_user_id(img) -> int | None:
    for _, text, _ in _ocr(img):
        m = USER_ID_RE.search(text)
        if m:
            return int(m.group(1))
    return None


def capture_ingame_ids(max_scrolls: int = 150) -> int:
    """Second pass over the guild list: for every member with no stored in-game
    User ID, tap their profile avatar, OCR 'User ID:' from the popup, store it,
    then Back out (which leaves the list at the same scroll position). No-op when
    every member already has an ID. Returns the count newly captured."""
    needing = members_needing_ingame_id()
    if not needing:
        print("All members have an in-game ID, skipping ID capture.")
        return 0

    targets = {name: mid for mid, name in needing}
    # Resolve on-screen cards the SAME way the main scan does (corrections,
    # decorative-tag stripping, fuzzy) so a card the scan can identify, this pass
    # can too · raw fuzzy alone missed tag-prefixed/known-misread names.
    resolve = make_roster_resolver()
    print(f"Capturing in-game IDs for {len(targets)} member(s): {', '.join(targets)}")

    scroll_to_top()
    captured = 0
    no_change = 0

    for _ in range(max_scrolls):
        if not targets:
            break
        # Multi-sample this stop: union the targets resolved across N frames before
        # tapping any. The list doesn't move between frames or across a tap/Back
        # cycle, so a y read here stays valid for the tap.
        found: dict[str, int] = {}   # canonical name -> anchor_y
        last_img = None
        for f in range(FRAMES_PER_STOP):
            if f:
                time.sleep(0.4)
            last_img = screenshot()
            for name, anchor_y in parse_member_anchors(_ocr(last_img)):
                canonical = resolve(name)
                if canonical in targets and canonical not in found:
                    found[canonical] = anchor_y

        for canonical, anchor_y in found.items():
            if canonical not in targets:
                continue
            member_id = targets[canonical]
            tap(AVATAR_X, anchor_y + AVATAR_Y_OFFSET)
            time.sleep(1.5)
            user_id = _read_user_id(screenshot())
            back()
            time.sleep(1.0)
            if user_id is None:
                print(f"  {canonical}: User ID not read, will retry next scan.")
                continue
            set_ingame_id(member_id, user_id)
            del targets[canonical]
            captured += 1
            print(f"  {canonical}: User ID {user_id} stored.")

        scroll_down()
        time.sleep(1.2)
        curr_img = screenshot()
        if not screen_changed(last_img, curr_img):
            no_change += 1
            if no_change >= 2:
                break
        else:
            no_change = 0

    if targets:
        print(f"ID capture: {len(targets)} not found this pass: {', '.join(targets)}")
    print(f"ID capture done, {captured} new ID(s).")
    return captured
