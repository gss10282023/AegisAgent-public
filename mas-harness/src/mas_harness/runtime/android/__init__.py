"""Android runtime helpers for MAS-Harness.

This package intentionally contains *thin* wrappers around adb/emulator
operations so that:
  * reset/snapshot operations are reproducible and auditable
  * oracles can query *real* device/app state (hard oracles)

The public Phase-0/1/2 smoke tests do not require a running emulator; these
modules are used in later phases when integrating AndroidWorld/MobileWorld
tasks.
"""
