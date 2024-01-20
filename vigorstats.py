import argparse
import asyncio
import logging
import re
import telnetlib3
import json

VIGOR_USERNAME = "admin"
VIGOR_PASSWORD = "admin"

VIGOR_130_VALUES = [
    {"label": "Running Mode"},
    {"label": "State"},
    {"label": "Power Management Mode"},
    {"label": "DS Actual Rate", "unit": "bps", "coerce": int},
    {"label": "DS Attainable Rate", "unit": "bps", "coerce": int},
    {"label": "DS Interleave Depth", "coerce": int},
    {"label": "NE Current Attenuation", "unit": "dB", "coerce": int},
    {"label": "NE CRC Count", "coerce": int},
    {"label": "NE ES Count", "coerce": int},
    {"label": "US Actual Rate", "unit": "bps", "coerce": int},
    {"label": "US Attainable Rate", "unit": "bps", "coerce": int},
    {"label": "US Interleave Depth", "coerce": int},
    {"label": "Cur SNR Margin", "unit": "dB", "coerce": int},
    {"label": "FE CRC Count", "coerce": int},
    {"label": "FE  ES Count", "coerce": int},
    {"label": "Far Current Attenuation", "unit": "dB", "coerce": int},
    {"label": "Far SNR Margin", "unit": "dB", "coerce": int},
    {"label": "Xdsl Reset Times", "coerce": int},
    {"label": "Xdsl Link  Times", "coerce": int},
]

VIGOR_130_MORE_VALUES = [
    {"label": "Trellis", "coerce": bool},
    {"label": "Bitswap", "coerce": bool},
    {"label": "ReTxEnable", "coerce": bool},
    {"label": "VirtualNoise", "coerce": bool},
    {"label": "LOS", "desc": "Loss Of Signal Count", "coerce": int},
    {"label": "LOF", "desc": "Loss Of Frame Count", "coerce": int},
    {"label": "LPR", "desc": "Loss Of Power Count", "coerce": int},
    {"label": "LOM", "desc": "Loss Of Margin Count", "coerce": int},
    {"label": "SosSuccess", "desc": "Successful SOS Procedure Count", "coerce": int},
    {"label": "NCD", "desc": "No Cell Delineation Failure Count", "coerce": int},
    {"label": "LCD", "desc": "Loss Of Cell Delineation Failure Count", "coerce": int},
    {"label": "FECS", "desc": "Forward Error Correction Seconds", "coerce": int},
    {"label": "ES", "desc": "Errored Seconds", "coerce": int},
    {"label": "SES", "desc": "Severely Errored Seconds", "coerce": int},
    {"label": "LOSS", "desc": "Loss Of Signal Seconds", "coerce": int},
    {"label": "UAS", "desc": "Unavailable Seconds", "coerce": int},
    {"label": "HECError", "desc": "Header Error Check Error Count", "coerce": int},
    {"label": "CRC", "desc": "CRC Error Count", "coerce": int},
    {"label": "INP", "desc": "Impulse Noise Protection", "coerce": int},
    {"label": "InterleaveDelay", "desc": "Interleave Delay", "coerce": int},
    {"label": "NFEC", "coerce": int},
    {"label": "RFEC", "coerce": int},
    {"label": "LSYMB", "coerce": int},
    {"label": "INTLVBLOCK", "coerce": int},
]


async def read_until(reader, text, retries=5, sleep=0.2):
    """
    Try to read from the reader object until the returned data
    ends with the provided text.
    """
    # FIXME: This doesn't actually wait for 1 second (5 * 0.2s) due to the await
    while retries:
        data = await reader.read(1024)
        if data.endswith(text):
            return data

        retries -= 1
        await asyncio.sleep(sleep)

    raise Exception("Did not find expected text")


async def get_vigor_stats(reader, writer):
    data = None

    try:
        logging.debug("Waiting for username request")
        await read_until(reader, "Account:")
        logging.debug("Sending username")
        writer.write(VIGOR_USERNAME + "\r\n")

        logging.debug("Waiting for password request")
        await read_until(reader, "Password: ")
        logging.debug("Sending password")
        writer.write(VIGOR_PASSWORD + "\r\n")

        logging.debug("Waiting for shell prompt")
        await read_until(reader, "> ")

        # This returns the majority of statistics
        writer.write("vdsl status\r\n")
        await asyncio.sleep(1)
        data = await reader.read(4096)

        # This returns additional stats
        writer.write("vdsl status more\r\n")
        await asyncio.sleep(1)
        more_data = await reader.read(8096)

    except Exception as exc:
        logging.exception("Could not process output from modem")

    output = dict()

    # Parse the basic VDSL status data
    logging.info("Parsing %d bytes of basic output", len(data))
    for value in VIGOR_130_VALUES:
        # Build a key from the label. At least one label has two spaces,
        # squash these to one before converting to underscore.
        key = value["label"].lower().replace("  ", " ").replace(" ", "_")

        if value["label"] not in data:
            logging.error("Could not find %s in data", value["label"])
            continue

        regex = "{}\s+:\s+(\w+)\s".format(value["label"])
        unit = value.get("unit", None)

        if unit:
            regex += "+" + unit

        match = re.search(regex, data)
        if not match:
            logging.error("Did not find information in output: %s", value["label"])
            continue

        coerce_fn = value.get("coerce")
        if coerce_fn:
            converted = coerce_fn(match.group(1))
        else:
            converted = match.group(1)

        output[key] = converted

    # Parse the extended VDSL status data
    # These are all integers, so one single regex can be used. The first number on each line
    # is the near end, the second is far end.
    # FECS         :   345097            633047 (seconds)
    logging.info("Parsing %d bytes of extended output", len(more_data))
    for value in VIGOR_130_MORE_VALUES:
        key = value["label"].lower().replace("  ", " ").replace(" ", "_")
        # Convert the key to something more readable
        if desc := value.get("desc", None):
            key = desc.lower().replace(" ", "_")

        regex = "{}\s+:\s+(\d+)\s+(\d+)".format(value["label"])

        match = re.search(regex, more_data)
        if not match:
            logging.error("Did not find information in output: %s", value["label"])
            continue

        coerce_fn = value.get("coerce")
        if coerce_fn:
            near_end = coerce_fn(match.group(1))
            far_end = coerce_fn(match.group(2))
        else:
            near_end = match.group(1)
            far_end = match.group(2)

        output[key + "_near"] = near_end
        output[key + "_far"] = far_end

    print(json.dumps(output))


def main():
    # TODO: Find a better way of passing arguments to the callback
    global VIGOR_USERNAME
    global VIGOR_PASSWORD

    parser = argparse.ArgumentParser()
    parser.add_argument("ip", help="IP address of the modem to query")
    parser.add_argument(
        "-u",
        "--username",
        default="admin",
        help="username with permissions to query VDSL stats (default 'admin')",
    )
    parser.add_argument(
        "-p", "--password", default="admin", help="password (default 'admin')"
    )
    parser.add_argument("-d", "--debug", action="store_true", help="print debug output")
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    VIGOR_USERNAME = args.username
    VIGOR_PASSWORD = args.password

    try:
        loop = asyncio.get_event_loop()
        coro = telnetlib3.open_connection(args.ip, 23, shell=get_vigor_stats)
        reader, writer = loop.run_until_complete(coro)
        loop.run_until_complete(writer.protocol.waiter_closed)
    except ConnectionRefusedError:
        logging.error("Could not connect to %s", args.ip)
    except KeyboardInterrupt:
        logging.error("User cancelled with ^C")


if __name__ == "__main__":
    main()
