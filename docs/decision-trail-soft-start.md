# Decision trail: the amplifier rail's soft start

This page follows a single design decision from start to finish. It covers how
the agents found the problem, who changed what, what was checked, and where the
checking stopped. The decision is the soft start on Q1, the P-channel MOSFET
that switches the 20 V USB-C input onto the amplifier rail (PVDD). PVDD feeds
several hundred microfarads of bulk capacitance. If Q1 turns on too fast, the
charger sees a current spike at every turn-on. That spike can trip the
charger's protection, and it can push Q1 past its safe operating area.

**In short:** the problem was found in two separate reviews on two days,
and it was fixed twice. The first fix was not enough, and the second review
caught that. The final circuit simulates at about 0.41 A peak inrush. Nobody
has measured it on a built board, so the trail ends at simulation.

## The original circuit (28 October)

- Q1 is an AO4485 P-channel MOSFET from VBUS to PVDD. R5 (100 kΩ) holds its
  gate at VBUS, and Q4 (an MMBT3904) pulls the gate down through R6 (47 kΩ).
  Q4's emitter sits on the USB-C PD controller's power-good line. That means
  PVDD switches on only when the firmware asks for it and the 20 V contract
  is in place.
- The soft-start capacitor C2 (220 nF) went from Q1's gate to its source.
- At that point the bulk capacitance on PVDD was 440 µF. It later became
  690 µF (C58 470 µF + C59 220 µF).

## Review 1: the fresh-context schematic review (28 October)

The board flow ends with a schematic review by a review agent that saw none
of the design history. It reads only the outputs. The electrical rule check
had passed with 0 errors and 0 warnings. That pass meant little: every pin in
the imported symbols was typed as passive, so the check could not find
driver conflicts ([snag #13](../hardware/HWDE-SNAGS.md)). Even with correct
pin types, an electrical rule check does not model how fast a capacitor
charges. This was a question for analysis or simulation.

The reviewer's finding E1, the review's only error
([review](../hardware/reports/review-schematic.md)):

> A gate-source capacitor does nothing to the drain slew: once the gate
> passes about −2 V, Vgs keeps moving at ~1.6 V/ms and with gfs ~25–30 S the
> FET can pass tens of amps within ~0.3 ms.
>
> That is an estimated 20–40 A peak drawn from a 3.25–5 A PD source.

The 20–40 A figure is the reviewer's hand estimate for the original circuit
into 440 µF. Nobody simulated the original circuit. The reviewer proposed a
Miller capacitor, which goes from gate to drain and slows the drain voltage
directly.

**First fix.** The agent that built the board took the reviewer's proposal. It
recorded this in the [response](../hardware/reports/review-schematic-response.md):
"C2 is now 100 nF gate-drain (Miller) through R15 1k … ~1.6 A into the 690 µF
bulk, a 6 ms ramp." That figure was a hand estimate too. The review's full
tally was 1 error and 14 warnings: 9 were fixed or mitigated, and 5 were
accepted with reasons.

## Review 2: the first-spin review (29 October)

The person did not ask about the soft start. On the evening of 28 October,
they set a bar for the whole design: "it needs to like definitely work. Like
i cant have no like maybe maybe not." The agents answered with an independent
first-spin review of every board in the family. A separate power reviewer
built simulation decks for the power path. Its two findings on the Lite were
both about the Miller fix:

- **POWER-3.** The Miller capacitor coupled every rising edge on VBUS into
  Q1's gate. That turned Q1 on with the firmware off. The simulated peaks
  were 20.6 A for a 30 V/ms edge (a PD step from 5 to 20 V), 6.1 A on plug-in
  and 2.0 A at 3 V/ms.
- **POWER-4.** Even when the firmware turned Q1 on deliberately, the turn-on
  sat at the edge of the MOSFET's safe operating area: 2.49 A from the source,
  43 W peak and 0.134 J in about 5 ms.

So the first fix's 1.6 A hand estimate did not survive simulation.

**Second fix.** R6 went from 47 kΩ to 220 kΩ, and a new capacitor C19 was
added from gate to source as a hold-off. The Miller capacitor C2 stayed. C19
holds the gate at VBUS through a fast input edge, and the larger R6 slows the
deliberate turn-on. The review text gave C19 as 2.2 µF, which is the value
the simulations used. The board was built with 4.7 µF/16 V, because a
ceramic capacitor loses much of its capacitance under DC bias and C19 only
ever sees up to about 6.6 V. The comment in
[root.py](../hardware/kicad/gen/root.py) gives the reasoning.

Before and after, on the same simulation deck (firmware turns PVDD on at
20 V):

| | Miller capacitor only | Miller + C19 + R6 220 kΩ |
|---|---:|---:|
| Peak current from the source | 2.49 A | 0.41 A |
| Peak power in Q1 | 43 W | 5.8 W |
| Energy in Q1 | 0.134 J | 0.138 J |
| Ramp | about 5 ms | about 28 ms (10–90 %) |
| PD step at 30 V/ms, firmware off | Q1 turns on (20.6 A) | Q1 stays off: Vsg 0.78 V against a 1.7 V minimum threshold |

The energy barely changes. The rail still has to be charged. What changes is
that the energy is spread over a longer time, so peak power falls to well
inside the safe operating area.

## How the fix was accepted

- The changes went in on 29 October. On 30 October they were merged through
  a pull request on the agents' working repository, together with the other
  first-spin fixes.
- The pull request records an incomplete check: "The final independent
  fix-check agent never finished, so the ECO edits are checked only by
  gates, not a second read." The automated gates passed.
- A later second read on 29 October found a side effect. The new gate
  divider gives Q1 only about 0.31 × VBUS of gate drive. That is too little
  to turn on at 9 V, and the extra RC holds PVDD off for 80–210 ms after the
  firmware asks for it. The agents handled this in firmware, not in the
  circuit. The amplifier now starts only on a contract of 14 V or more, with
  a brownout floor of 13 V, and it waits 400 ms after PVDD switches before
  it leaves shutdown ([hifi_power.c](../firmware/components/hifi/core/hifi_power.c)).
- The board files' own simulation of the final circuit, across component
  tolerance, gives 0.36–0.43 A, 4.9–6.3 W and 72–126 ms to 90 %
  ([pvdd_switch.py](../hardware/sims/pvdd_switch.py),
  [results](../hardware/sims/out_pvdd_switch.txt)). The ramp time there
  includes the gate RC's delay before Q1 starts to conduct.

No disagreement between agents was found in the record. Each fix was taken
as the reviewer proposed it.

## Who did what

| Step | Who |
|---|---|
| Set the bar that started review 2 | The person: "it needs to like definitely work" |
| Ran review 1, found the gate-source capacitor | A fresh-context review agent, as a standard step of the board flow |
| Chose the Miller fix | The agent building the board, taking the reviewer's proposal |
| Found that the Miller fix was not enough | The power reviewer in review 2, by simulation |
| Chose and simulated the hold-off fix | The power reviewer proposed and simulated it, and it was applied to the board |
| Found the low-voltage side effect | A second-read review agent |
| Handled it in firmware | The agents, in the power-sequencing code |
| Approved the order with this circuit | The person, on 5 November, after all gates passed |

The person did not name the capacitor, choose a fix or comment on inrush at
any point. Their part was the "must work first time" bar that led to the
second review.

## Where it stops

- **Not measured on a built board.** The trail ends at simulation. The five
  boards switch PVDD on correctly, and their self-tests read it at 19.8–19.9 V
  on a 20 V contract. Nobody has put a scope on the inrush. The scope
  capture is still a planned step in [bringup.md](bringup.md).
- The 20–40 A figure is a hand estimate at 440 µF. The 0.41 A figure is a
  simulation at 690 µF of a different circuit. Compare them as "before and
  after the two reviews", not as one change.
- The simulations use a MOSFET model fitted by the agents to the AO4485
  datasheet, and assumed cable and charger impedances. A charger with a faster or slower VBUS edge than the ones
  simulated has not been tried with a current probe.
