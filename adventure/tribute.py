# -*- coding: utf-8 -*-
import asyncio
import contextlib
import logging
import time
from typing import Optional

import discord
from redbot.core import commands
from redbot.core.i18n import Translator
from redbot.core.utils.chat_formatting import humanize_number

from .abc import AdventureMixin
from .bank import bank
from .helpers import smart_embed

_ = Translator("Adventure", __file__)

log = logging.getLogger("red.cogs.adventure")


TRIBUTE_BASE_COSTS = {5 * 60: 500_000, 10 * 60: 700_000, 15 * 60: 1_000_000}
TRIBUTE_EXTRA_COSTS = {"transcended": 100_000, "immortal": 250_000, "divine": 300_000}
TRIBUTE_TYPE_LABELS = {
    None: ("\N{CROSSED SWORDS}\N{VARIATION SELECTOR-16} Boss/Miniboss", "Boss & Miniboss Blessing"),
    "transcended": ("\N{SPARKLES} Transcended", "Transcended Blessing"),
    "immortal": ("\N{SKULL} Immortal", "Immortal Blessing"),
    "divine": ("\N{GLOWING STAR} Divine (All)", "Divine Blessing"),
}
TRIBUTE_BUFF_DESCRIPTIONS = {
    None: ["• Boss & Miniboss encounters: **~30%** (base ~3%)"],
    "transcended": [
        "• Boss & Miniboss encounters: **~30%** (base ~3%)",
        "• Transcended encounters: **~30%** (base ~9%)",
    ],
    "immortal": [
        "• Boss & Miniboss encounters: **~30%** (base ~3%)",
        "• Immortal attribute: **significantly more likely**",
    ],
    "divine": [
        "• Boss & Miniboss encounters: **~30%** (base ~3%)",
        "• Transcended encounters: **~30%** (base ~9%)",
        "• Immortal attribute: **significantly more likely**",
    ],
}


class _TributeBase(discord.ui.View):
    def __init__(self, ctx: commands.Context):
        super().__init__(timeout=30)
        self.ctx = ctx
        self.cancelled = False
        self.timed_out = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.ctx.author:
            await interaction.response.send_message(_("This is not your tribute."), ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        self.timed_out = True

    def _add_cancel(self):
        btn = discord.ui.Button(
            label=_("Cancel"),
            style=discord.ButtonStyle.red,
            emoji="\N{HEAVY MULTIPLICATION X}\N{VARIATION SELECTOR-16}",
            row=1,
        )
        btn.callback = self._cancel_callback
        self.add_item(btn)

    async def _cancel_callback(self, interaction: discord.Interaction):
        self.cancelled = True
        self.stop()
        await interaction.response.defer()


class TributeTypeView(_TributeBase):
    def __init__(self, ctx: commands.Context):
        super().__init__(ctx)
        self.chosen_type: Optional[str] = None
        self._chosen = False
        for buff_type, (btn_label, _) in TRIBUTE_TYPE_LABELS.items():
            btn = discord.ui.Button(label=btn_label, style=discord.ButtonStyle.blurple, row=0)
            btn.callback = self._make_callback(buff_type)
            self.add_item(btn)
        self._add_cancel()

    def _make_callback(self, buff_type):
        async def callback(interaction: discord.Interaction):
            self.chosen_type = buff_type
            self._chosen = True
            self.stop()
            await interaction.response.defer()
        return callback


class TributeDurationView(_TributeBase):
    DURATIONS = [(5 * 60, "5 min"), (10 * 60, "10 min"), (15 * 60, "15 min")]

    def __init__(self, ctx: commands.Context, buff_type: Optional[str]):
        super().__init__(ctx)
        self.chosen_duration: Optional[int] = None
        self.chosen_cost: Optional[int] = None
        self.went_back = False
        extra = TRIBUTE_EXTRA_COSTS.get(buff_type, 0) if buff_type else 0
        for seconds, label in self.DURATIONS:
            cost = TRIBUTE_BASE_COSTS[seconds] + extra
            btn = discord.ui.Button(
                label=f"{label} — {humanize_number(cost)} coins",
                style=discord.ButtonStyle.blurple,
                row=0,
            )
            btn.callback = self._make_callback(seconds, cost)
            self.add_item(btn)
        back = discord.ui.Button(label=_("\N{LEFTWARDS ARROW} Back"), style=discord.ButtonStyle.grey, row=1)
        back.callback = self._back_callback
        self.add_item(back)
        self._add_cancel()

    def _make_callback(self, seconds: int, cost: int):
        async def callback(interaction: discord.Interaction):
            self.chosen_duration = seconds
            self.chosen_cost = cost
            self.stop()
            await interaction.response.defer()
        return callback

    async def _back_callback(self, interaction: discord.Interaction):
        self.went_back = True
        self.stop()
        await interaction.response.defer()


class TributeCommands(AdventureMixin):
    """Tribute command — pay coins to buff channel encounter rates."""

    def _get_channel_buff(self, channel_id: int) -> Optional[dict]:
        buff = self._channel_buffs.get(channel_id)
        if buff and buff["expires"] > time.time():
            return buff
        if channel_id in self._channel_buffs:
            del self._channel_buffs[channel_id]
        return None

    @commands.hybrid_command(name="tribute")
    @commands.guild_only()
    async def tribute(self, ctx: commands.Context):
        """Pay tribute to the god for a channel encounter buff.

        Spend coins to temporarily increase boss, miniboss, transcended,
        and/or immortal encounter rates in the current channel.
        """
        await ctx.defer()
        cooldown = await self.config.guild(ctx.guild).tribute_cooldown()
        last_used = await self.config.guild(ctx.guild).tribute_last_used()
        remaining = cooldown - (time.time() - last_used)
        if remaining > 0:
            hours, rem = divmod(int(remaining), 3600)
            minutes, secs = divmod(rem, 60)
            if hours:
                remaining_str = _("{h}h {m}m").format(h=hours, m=minutes)
            elif minutes:
                remaining_str = _("{m}m {s}s").format(m=minutes, s=secs)
            else:
                remaining_str = _("{s}s").format(s=secs)
            return await smart_embed(
                ctx,
                _("The gods are still recovering from the last tribute. Try again in **{time}**.").format(
                    time=remaining_str
                ),
            )

        if self._get_channel_buff(ctx.channel.id):
            return await smart_embed(ctx, _("This channel already has an active blessing. Wait for it to expire."))

        if ctx.channel.id in self._active_tribute_menus:
            return await smart_embed(ctx, _("Someone is already paying tribute in this channel. Wait for them to finish."))

        self._active_tribute_menus.add(ctx.channel.id)
        try:
            god = await self.config.god_name()
            guild_god = await self.config.guild(ctx.guild).god_name()
            if guild_god:
                god = guild_god

            colour = await ctx.embed_colour()
            cancelled_embed = discord.Embed(title=_("Tribute cancelled."), colour=colour)
            timeout_embed = discord.Embed(
                title=_("\N{ANGRY FACE} You dare waste a god's time?"),
                description=_(
                    "**{god}** graced {channel} with an audience, yet you stood there in silence.\n"
                    "The divine presence withdraws — do not return until you are ready to kneel."
                ).format(god=god, channel=ctx.channel.mention),
                colour=discord.Colour.dark_red(),
            )

            type_embed = discord.Embed(
                title=_("\N{PERSON WITH FOLDED HANDS} Pay Tribute to {god}?").format(god=god),
                description=_(
                    "You kneel before the altar of **{god}**, seeking divine favour upon {channel}.\n\n"
                    "Choose the blessing you wish to invoke:"
                ).format(god=god, channel=ctx.channel.mention),
                colour=colour,
            )

            msg = None
            buff_type = None

            while True:
                type_view = TributeTypeView(ctx)
                if msg is None:
                    msg = await ctx.send(embed=type_embed, view=type_view)
                else:
                    await msg.edit(embed=type_embed, view=type_view)
                await type_view.wait()

                if type_view.timed_out:
                    return await msg.edit(embed=timeout_embed, view=None)
                if type_view.cancelled or not type_view._chosen:
                    return await msg.edit(embed=cancelled_embed, view=None)

                buff_type = type_view.chosen_type
                _ignored, blessing_label = TRIBUTE_TYPE_LABELS[buff_type]
                buff_lines = TRIBUTE_BUFF_DESCRIPTIONS[buff_type]
                extra = TRIBUTE_EXTRA_COSTS.get(buff_type, 0) if buff_type else 0

                duration_embed = discord.Embed(
                    title=_("\N{PERSON WITH FOLDED HANDS} {blessing} — {god}").format(blessing=blessing_label, god=god),
                    description=_(
                        "**Buffs granted (rough estimates only):**\n{buffs}\n\n"
                        "_Results may vary — the gods do not make guarantees._\n\n"
                        "Choose how long the blessing should last:"
                    ).format(buffs="\n".join(buff_lines)),
                    colour=colour,
                )

                duration_view = TributeDurationView(ctx, buff_type)
                await msg.edit(embed=duration_embed, view=duration_view)
                await duration_view.wait()

                if duration_view.timed_out:
                    return await msg.edit(embed=timeout_embed, view=None)
                if duration_view.cancelled or duration_view.chosen_duration is None:
                    return await msg.edit(embed=cancelled_embed, view=None)

                if duration_view.went_back:
                    continue

                break

            await msg.edit(view=None)

            if self._get_channel_buff(ctx.channel.id):
                return await msg.edit(
                    embed=discord.Embed(
                        title=_("A blessing was just applied to this channel."),
                        description=_("Your tribute was not charged."),
                        colour=discord.Colour.red(),
                    )
                )

            try:
                await bank.withdraw_credits(ctx.author, duration_view.chosen_cost)
            except ValueError:
                return await msg.edit(
                    embed=discord.Embed(
                        title=_("Not enough coins."),
                        description=_("You need {cost} coins to pay this tribute.").format(
                            cost=humanize_number(duration_view.chosen_cost)
                        ),
                        colour=discord.Colour.red(),
                    )
                )

            duration_label = next(label for s, label in TributeDurationView.DURATIONS if s == duration_view.chosen_duration)
            now = time.time()
            self._channel_buffs[ctx.channel.id] = {
                "expires": now + duration_view.chosen_duration,
                "transcended": buff_type in ("transcended", "divine"),
                "immortal": buff_type in ("immortal", "divine"),
            }
            await self.config.guild(ctx.guild).tribute_last_used.set(now)

            await msg.edit(
                embed=discord.Embed(
                    title=_("\N{SPARKLES} {god} accepts your tribute!").format(god=god),
                    description=_(
                        "**{god}** smiles upon {channel}. For the next **{duration}**, encounters here are blessed.\n\n{buffs}"
                    ).format(
                        god=god,
                        channel=ctx.channel.mention,
                        duration=duration_label,
                        buffs="\n".join(buff_lines),
                    ),
                    colour=discord.Colour.gold(),
                )
            )

            task = asyncio.create_task(
                self._tribute_expiry_notify(ctx.channel, ctx.author, god, duration_view.chosen_duration)
            )
            self.tasks[f"tribute_{ctx.channel.id}"] = task
        finally:
            self._active_tribute_menus.discard(ctx.channel.id)

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
