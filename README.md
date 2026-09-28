# Infinite Monkeys AI Game Decomper

Proof of concepts project to have local AI models decomp retro video games.

Currently does not work at all.

## High Level Plan

1. User Provides Game ROM
2. ROM gets unpacked
3. Local AI agent keeps trying to recreate the source code and compile it until it matches the unpacked game rom. (verification scripts)

## Other Notes

- Everything runs inside a container to avoid agent going rogue
- Designed for free local agents to avoid burning an absurd amount of money on a decomp that doesn't work.
