"""Add or correct one Guild Duel Battle Report row by hand, or list who the
last scan missed.

  python src/add_guild_duel.py --missing
  python src/add_guild_duel.py "Name" <rank> <crests>
  python src/add_guild_duel.py "Name" <rank> <crests> --period 2026-10-12

Period defaults to the week Battle Report is currently showing. Re-adding the
same member overwrites their row for that period."""
import argparse

from db import (init_db, _utcnow, guild_duel_period_key, get_guild_duel_missing,
                resolve_names, names_for_ids, save_guild_duel)


def main() -> None:
    cli = argparse.ArgumentParser()
    cli.add_argument("name", nargs="?")
    cli.add_argument("rank", nargs="?", type=int)
    cli.add_argument("crests", nargs="?", type=int)
    cli.add_argument("--period", default=None, help="period_start ISO date (default: current Battle Report week)")
    cli.add_argument("--missing", action="store_true", help="list active members with no row for the period")
    args = cli.parse_args()

    init_db()
    period = args.period or guild_duel_period_key(_utcnow())

    if args.missing:
        missing = get_guild_duel_missing(period)
        print(f"Period {period}: {len(missing)} active member(s) missing.")
        for n in missing:
            print(f"  {n}")
        return

    if args.name is None or args.rank is None or args.crests is None:
        cli.error("need name, rank and crests (or use --missing)")

    resolved, unmatched = resolve_names([args.name])
    if unmatched:
        raise SystemExit(f"No member matches '{args.name}'. Check spelling or /review.")
    member_id = resolved[args.name]
    save_guild_duel([{"member_id": member_id, "rank": args.rank, "crests": args.crests}], period)
    canonical = names_for_ids([member_id])[member_id]
    print(f"Saved {canonical}: rank #{args.rank}, {args.crests} crests, period {period}.")


if __name__ == "__main__":
    main()
