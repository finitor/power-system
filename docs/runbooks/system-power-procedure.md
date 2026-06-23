# System Power Procedure

Manual procedure for a full system power-down and power-up. Follow steps in order.

---

## A. Power Down

**a1.** Disconnect PV array 0 from charge controller 0 (MidNite Solar Classic) using the
20A DC breaker in the MidNite Solar breaker enclosure.

**a2.** Disconnect the battery from charge controller 0 using the 100A DC breaker in the
MidNite Solar breaker enclosure.

**a3.** Disconnect PV array 1 from charge controller 1 (EPEver) using the 20A DC breaker
on the left mounting board.

**a4.** Disconnect the battery from charge controller 1 using the rotary disconnect switch
on the left mounting board.

**a5.** Power off both batteries using their pushbuttons. Confirm no LEDs are lit on the
battery control panels.

---

## B. Power Up

**b1.** Confirm the PV array breakers and battery disconnects for both charge controllers
are OFF (steps a1–a4 above) before energising the batteries.

**b2.** Power on both batteries using their pushbuttons. Confirm status LEDs are lit.

**b3.** Connect the battery to charge controller 1 using the rotary disconnect switch on
the left mounting board (position ON).

**b4.** Connect PV array 1 to charge controller 1 using the 20A DC breaker on the left
mounting board (position ON).

**b5.** Connect the battery to charge controller 0 using the 100A DC breaker in the
MidNite Solar breaker enclosure (position ON).

**b6.** Connect PV array 0 to charge controller 0 using the 20A DC breaker in the
MidNite Solar breaker enclosure (position ON).

**b7.** On the Magnum ME-RC50 remote, press the Inverter button once to wake the remote,
then press it once more to turn the inverter ON. The remote should indicate
**Inverting**. With load present the inverter can be heard humming.

> **Note:** The Magnum inverter does not save its ON/OFF state across power cycles —
> it always resets to OFF when DC is removed, regardless of prior state. The manual
> button press in b7 is always required. See the inverter-charger subsystem doc for
> discussion of a supervisor-driven workaround.
