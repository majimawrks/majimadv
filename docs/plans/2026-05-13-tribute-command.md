# Tribute Command Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `tribute` command that lets a user spend coins to buff the channel's adventure encounter rates for boss/miniboss (always), and optionally transcended and/or immortal, for a chosen duration.

**Architecture:** Store active channel buffs in an in-memory dict `self._channel_buffs` on the cog (no DB persistence needed for short-lived buffs). A `TributeView` handles the duration-selection UI. Three existing methods — `get_challenge`, `update_monster_roster`, and `_simple` — each receive a `channel_buff` dict and branch on it. A fire-and-forget `asyncio` task handles the end-of-buff notification.

**Tech Stack:** discord.py `discord.ui.View`, Red Economy (`bank`), Red `commands`, existing `AdventureMixin` / `abc.py` pattern.

---

### Task 1: Add `_channel_buffs` to the cog and a helper to read it

**Files:**
- Modify: `adventure/abc.py:60` (add `_channel_buffs` declaration)
- Modify: `adventure/adventure.py` (add `_get_channel_buff` helper, initialise dict in `__init__` or `cog_load`)

**Step 1: Add the attribute declaration in `abc.py`**

In `adventure/abc.py`, inside `AdventureMixin.__init__`, after `self.tasks = {}` (line 60), add:

```python
self._channel_buffs: dict = {}
```

**Step 2: Add the helper method in `adventure/adventure.py`**

Find the `get_challenge` method (around line 692). Just above it, add:

```python
def _get_channel_buff(self, channel_id: int) -> dict | None:
    buff = self._channel_buffs.get(channel_id)
    if buff and buff["expires"] > time.time():
        return buff
    if channel_id in self._channel_buffs:
        del self._channel_buffs[channel_id]
    return None
```

Make sure `import time` is present at the top of `adventure.py` (it likely already is — verify).

**Step 3: Commit**

```bash
git add adventure/abc.py adventure/adventure.py
git commit -m "feat(tribute): add _channel_buffs store and _get_channel_buff helper"
```

---

### Task 2: Modify `get_challenge` to accept and apply boss/miniboss buff

**Files:**
- Modify: `adventure/adventure.py:692-713` (`get_challenge`)

**Step 1: Update the signature**

Change:
```python
async def get_challenge(self, monsters: Dict[str, Monster], rng: Random):
```
To:
```python
async def get_challenge(self, monsters: Dict[str, Monster], rng: Random, channel_buff: dict | None = None):
```

**Step 2: Apply the buff inside the loop**

Change the normal-monster weight line from:
```python
if not stats["boss"] and not stats["miniboss"]:
    break_at = rng.randint(1, 15)
    possible_monsters.extend([m for i in range(1, break_at)])
```
To:
```python
if not stats["boss"] and not stats["miniboss"]:
    upper = 2 if channel_buff else 15
    break_at = rng.randint(1, upper)
    possible_monsters.extend([m for i in range(1, break_at)])
```

This reduces each normal monster's average copies from ~7 to ~0.5, pushing boss+miniboss from ~3% to ~30%.

**Step 3: Commit**

```bash
git add adventure/adventure.py
git commit -m "feat(tribute): buff boss/miniboss rate in get_challenge when channel buff active"
```

---

### Task 3: Modify `update_monster_roster` to accept and apply transcended buff

**Files:**
- Modify: `adventure/adventure.py:829-869` (`update_monster_roster`)

**Step 1: Update the signature**

Locate:
```python
async def update_monster_roster(
```
The method currently takes `c` and `rng`. Add `channel_buff`:
```python
async def update_monster_roster(
    self,
    c=None,
    rng=None,
    channel_buff: dict | None = None,
) -> Tuple[Dict[str, Monster], float, bool]:
```

**Step 2: Apply the transcended buff**

After the existing transcended_chance roll (line ~847-849), the code checks `if transcended_chance == 5`. Change the two check sites to:

```python
# First check (unconditional monster_stats = 2.0 block):
transcended_hit = (
    (channel_buff and channel_buff.get("transcended") and random.random() < 0.30)
    or transcended_chance == 5
)
if transcended_hit:
    monster_stats = 2.0

# Second check (character-gated block inside `if c is not None:`):
if c is not None:
    if transcended_hit:
        monster_stats = 2 + max((c.rebirths // 10) - 1, 0)
        transcended = True
    elif c.rebirths >= 10:
        monster_stats = 1 + max((c.rebirths // 10) - 1, 0) / 2
```

Make sure `import random` is at the top of `adventure.py` (it already is).

**Step 3: Commit**

```bash
git add adventure/adventure.py
git commit -m "feat(tribute): buff transcended rate in update_monster_roster when channel buff active"
```

---

### Task 4: Modify `_simple` to look up the channel buff and thread it through

**Files:**
- Modify: `adventure/adventure.py:871-901` (`_simple`)

**Step 1: Look up buff and pass to `update_monster_roster`**

After `rng = Random(seed)` (line ~892), add:
```python
channel_buff = self._get_channel_buff(ctx.channel.id)
```

Change the existing call:
```python
monster_roster, monster_stats, transcended = await self.update_monster_roster(c=c, rng=rng)
```
To:
```python
monster_roster, monster_stats, transcended = await self.update_monster_roster(c=c, rng=rng, channel_buff=channel_buff)
```

**Step 2: Pass buff to `get_challenge`**

Change:
```python
challenge = await self.get_challenge(monster_roster, rng)
```
To:
```python
challenge = await self.get_challenge(monster_roster, rng, channel_buff=channel_buff)
```

**Step 3: Apply immortal buff to attribute selection**

Change:
```python
attribute = rng.choice(list(self.ATTRIBS.keys()))
```
To:
```python
if channel_buff and channel_buff.get("immortal"):
    attrib_pool = list(self.ATTRIBS.keys()) + ["n immortal"] * 9
    attribute = rng.choice(attrib_pool)
else:
    attribute = rng.choice(list(self.ATTRIBS.keys()))
```

This makes `"n immortal"` appear 9 extra times alongside the normal pool (10x more likely than any single other attribute).

**Step 4: Commit**

```bash
git add adventure/adventure.py
git commit -m "feat(tribute): wire channel_buff through _simple to get_challenge, update_monster_roster, and attribute selection"
```

---

### Task 5: Build the `TributeView`

**Files:**
- Modify: `adventure/adventure.py` (add `TributeView` class near the top, after imports, before the cog class — same pattern as other Views in `backpack.py`)

**Step 1: Add the class**

Add this class before the `Adventure` cog class definition:

```python
TRIBUTE_BASE_COSTS = {5 * 60: 500_000, 10 * 60: 700_000, 15 * 60: 1_000_000}
TRIBUTE_EXTRA_COSTS = {"transcended": 100_000, "immortal": 250_000, "divine": 300_000}

class TributeView(discord.ui.View):
    DURATIONS = [(5 * 60, "5 min"), (10 * 60, "10 min"), (15 * 60, "15 min")]

    def __init__(self, ctx: commands.Context, buff_type: str | None, god: str):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.buff_type = buff_type
        self.god = god
        self.chosen_duration: int | None = None
        self.chosen_cost: int | None = None
        extra = TRIBUTE_EXTRA_COSTS.get(buff_type, 0) if buff_type else 0
        for seconds, label in self.DURATIONS:
            cost = TRIBUTE_BASE_COSTS[seconds] + extra
            btn = discord.ui.Button(
                label=f"{label} — {humanize_number(cost)} coins",
                style=discord.ButtonStyle.blurple,
            )
            btn.callback = self._make_callback(seconds, cost)
            self.add_item(btn)
        cancel = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.red,
            emoji="\N{HEAVY MULTIPLICATION X}\N{VARIATION SELECTOR-16}",
        )
        cancel.callback = self._cancel_callback
        self.add_item(cancel)

    def _make_callback(self, seconds: int, cost: int):
        async def callback(interaction: discord.Interaction):
            if interaction.user != self.ctx.author:
                return await interaction.response.send_message("This is not your tribute.", ephemeral=True)
            self.chosen_duration = seconds
            self.chosen_cost = cost
            self.stop()
            await interaction.response.defer()
        return callback

    async def _cancel_callback(self, interaction: discord.Interaction):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("This is not your tribute.", ephemeral=True)
        self.stop()
        await interaction.response.defer()
```

Note: `humanize_number` is already imported in `adventure.py` — verify at top of file.

**Step 2: Commit**

```bash
git add adventure/adventure.py
git commit -m "feat(tribute): add TributeView with dynamic cost buttons per buff_type"
```

---

### Task 6: Add the `tribute` command

**Files:**
- Modify: `adventure/adventure.py` (add command after `_adventure`)
- Modify: `adventure/abc.py` (add abstract method stub)

**Step 1: Add abstract stub in `abc.py`**

In the `# adventure.py` section of `AdventureMixin`, after the `_adventure` abstract method, add:

```python
@abstractmethod
async def tribute(self, ctx: commands.Context, buff_type: Optional[str] = None):
    raise NotImplementedError()
```

**Step 2: Add the command in `adventure.py`**

After the `_adventure` command method, add:

```python
@commands.hybrid_command(name="tribute")
@commands.guild_only()
@commands.bot_has_permissions(embed_links=True)
async def tribute(self, ctx: commands.Context, buff_type: Optional[str] = None):
    """Pay tribute to the god for a channel encounter buff.

    buff_type options: transcended, immortal, divine (all buffs), or omit for boss/miniboss only.
    """
    if buff_type and buff_type.lower() not in ("transcended", "immortal", "divine"):
        return await smart_embed(ctx, _("Invalid buff type. Use: `transcended`, `immortal`, `divine`, or leave blank."))
    buff_type = buff_type.lower() if buff_type else None

    god = await self.config.god_name()
    guild_god = await self.config.guild(ctx.guild).god_name()
    if guild_god:
        god = guild_god

    extra = TRIBUTE_EXTRA_COSTS.get(buff_type, 0) if buff_type else 0
    buff_lines = ["• Boss & Miniboss encounters: **~30%** (base ~3%)"]
    if buff_type in ("transcended", "divine"):
        buff_lines.append("• Transcended encounters: **~30%** (base ~9%)")
    if buff_type in ("immortal", "divine"):
        buff_lines.append("• Immortal attribute: **significantly more likely**")
    buff_lines.append("")
    buff_lines.append("_Results may vary — the gods do not make guarantees._")

    costs_text = "\n".join(
        f"• {label}: **{humanize_number(TRIBUTE_BASE_COSTS[s] + extra)}** coins"
        for s, label in TributeView.DURATIONS
    )

    embed = discord.Embed(
        title=_("🙏 Pay Tribute to {god}?").format(god=god),
        description=_(
            "You kneel before the altar of **{god}**, offering coins in exchange for divine favour upon {channel}.\n\n"
            "**Buffs granted (rough estimates only):**\n{buffs}\n\n"
            "**Duration costs:**\n{costs}"
        ).format(
            god=god,
            channel=ctx.channel.mention,
            buffs="\n".join(buff_lines),
            costs=costs_text,
        ),
        colour=await ctx.embed_colour(),
    )

    view = TributeView(ctx, buff_type, god)
    msg = await ctx.send(embed=embed, view=view)
    await view.wait()
    await msg.edit(view=None)

    if view.chosen_duration is None:
        return await msg.edit(
            embed=discord.Embed(
                title=_("Tribute cancelled."),
                colour=await ctx.embed_colour(),
            )
        )

    try:
        await bank.withdraw_credits(ctx.author, view.chosen_cost)
    except Exception:
        return await msg.edit(
            embed=discord.Embed(
                title=_("Not enough coins."),
                description=_("You need {cost} coins to pay this tribute.").format(
                    cost=humanize_number(view.chosen_cost)
                ),
                colour=discord.Colour.red(),
            )
        )

    duration_label = next(label for s, label in TributeView.DURATIONS if s == view.chosen_duration)
    self._channel_buffs[ctx.channel.id] = {
        "expires": time.time() + view.chosen_duration,
        "transcended": buff_type in ("transcended", "divine"),
        "immortal": buff_type in ("immortal", "divine"),
    }

    await msg.edit(
        embed=discord.Embed(
            title=_("✨ {god} accepts your tribute!").format(god=god),
            description=_(
                "**{god}** smiles upon {channel}. For the next **{duration}**, encounters here are blessed.\n\n{buffs}"
            ).format(
                god=god,
                channel=ctx.channel.mention,
                duration=duration_label,
                buffs="\n".join(buff_lines[:-2]),  # exclude the disclaimer for confirmation
            ),
            colour=discord.Colour.gold(),
        )
    )

    asyncio.create_task(
        self._tribute_expiry_notify(ctx.channel, ctx.author, god, view.chosen_duration)
    )
```

**Step 3: Add the expiry notification coroutine**

Just after the `tribute` command, add:

```python
async def _tribute_expiry_notify(
    self,
    channel: discord.TextChannel,
    invoker: discord.Member,
    god: str,
    duration: int,
):
    await asyncio.sleep(duration)
    with contextlib.suppress(discord.HTTPException):
        await channel.send(
            _("The blessing of **{god}** has faded from {channel}, {mention}.").format(
                god=god,
                channel=channel.mention,
                mention=invoker.mention,
            )
        )
```

Make sure `contextlib` is imported at the top of `adventure.py` (it already is — verify).

**Step 4: Commit**

```bash
git add adventure/adventure.py adventure/abc.py
git commit -m "feat(tribute): add tribute command with TributeView, buff activation, and expiry notification"
```

---

### Task 7: Final verification

**Step 1: Reload the cog in a test bot and run each variant**

```
[p]tribute                  → boss/miniboss buff only
[p]tribute transcended      → boss/miniboss + transcended
[p]tribute immortal         → boss/miniboss + immortal
[p]tribute divine           → all three
```

Check that:
- Buttons show correct costs per variant
- Cancel exits cleanly
- Insufficient balance shows error embed
- After paying, confirmation embed appears
- After the buff duration, expiry message is sent mentioning the invoker
- Running `[p]adventure` during buff shows elevated boss/miniboss rates (use `[p]a <seed>` repeatedly to observe)

**Step 2: Commit any fixes found during testing**
