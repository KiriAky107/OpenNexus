"""Bounded four-ROI PNG decoder and source-pixel comparison, never a screenshot.

Only an actual compositor packet bound to the held GUI can prove presentation.
Decoding the original fixture or exercising this parser alone proves no device UI.
"""
import binascii
import hashlib
import struct
import zlib

from android_runtime_probe import IMAGE_BYTES, require

def digest(raw): return hashlib.sha256(raw).hexdigest()

MAGIC = b'OPENNEXUS_ROI1\n'


def decode_png(raw, width, height):
    require(type(raw) is bytes and 8 <= len(raw) <= 4096 and raw[:8] == b'\x89PNG\r\n\x1a\n', 'SYNC_IMAGE_PNG_BOUND')
    offset = 8; chunks = []; compressed = bytearray(); header = None; ended = False; data_ended = False
    while offset < len(raw):
        require(offset + 12 <= len(raw) and not ended, 'SYNC_IMAGE_PNG_TRUNCATED')
        length, = struct.unpack_from('>I', raw, offset); kind = raw[offset+4:offset+8]; offset += 8
        require(length <= 4096 and offset + length + 4 <= len(raw), 'SYNC_IMAGE_PNG_CHUNK_BOUND')
        body = raw[offset:offset+length]; crc, = struct.unpack_from('>I', raw, offset+length); offset += length+4
        require(binascii.crc32(kind+body) & 0xffffffff == crc, 'SYNC_IMAGE_PNG_CRC')
        require(kind in (b'IHDR', b'IDAT', b'IEND', b'sRGB', b'gAMA', b'cHRM', b'sBIT', b'iCCP'), 'SYNC_IMAGE_PNG_CHUNK_SCOPE')
        if not chunks: require(kind == b'IHDR', 'SYNC_IMAGE_PNG_HEADER_FIRST')
        if kind == b'IHDR':
            require(header is None and length == 13, 'SYNC_IMAGE_PNG_HEADER')
            header = struct.unpack('>IIBBBBB', body)
            require(header[:3] == (width, height, 8) and header[3] in (2,6) and header[4:] == (0,0,0), 'SYNC_IMAGE_PNG_FORMAT')
        elif kind == b'IDAT':
            require(header is not None and not data_ended and length > 0, 'SYNC_IMAGE_PNG_DATA_ORDER')
            compressed.extend(body)
        elif kind == b'IEND':
            require(length == 0 and compressed, 'SYNC_IMAGE_PNG_END'); ended = True
        else:
            require(kind not in chunks and not compressed, 'SYNC_IMAGE_PNG_METADATA_ORDER')
        if compressed and kind != b'IDAT': data_ended = True
        chunks.append(kind)
    require(ended and header is not None and 1 <= width <= 8 and 1 <= height <= 8, 'SYNC_IMAGE_PNG_CLOSED')
    channels = 4 if header[3] == 6 else 3; stride = width*channels; expected = height*(stride+1)
    inflater = zlib.decompressobj()
    decoded = inflater.decompress(bytes(compressed), expected+1)
    require(len(decoded) == expected and inflater.eof and not inflater.unused_data and not inflater.unconsumed_tail,
            'SYNC_IMAGE_PNG_INFLATED_BOUND')
    pixels = []; prior = bytearray(stride)
    for y in range(height):
        start = y*(stride+1); mode = decoded[start]; row = bytearray(decoded[start+1:start+1+stride])
        require(mode <= 4, 'SYNC_IMAGE_PNG_FILTER')
        for x in range(stride):
            a = row[x-channels] if x >= channels else 0; b = prior[x]; c = prior[x-channels] if x >= channels else 0
            if mode == 1: predict = a
            elif mode == 2: predict = b
            elif mode == 3: predict = (a+b)//2
            elif mode == 4:
                p = a+b-c; distances = (abs(p-a),abs(p-b),abs(p-c)); predict = (a,b,c)[distances.index(min(distances))]
            else: predict = 0
            row[x] = (row[x]+predict) & 255
        pixels.extend(tuple(row[x:x+channels]) + (() if channels == 4 else (255,)) for x in range(0,stride,channels))
        prior = row
    return pixels


def source_samples(image):
    require(type(image) is list and len(image)==4 and all(type(n) is int and 0<=n<=16384 for n in image)
            and image[0]<image[2] and image[1]<image[3], 'SYNC_IMAGE_PIXEL_GEOMETRY')
    side=min(image[2]-image[0],image[3]-image[1]);require(64<=side<=2048,'SYNC_IMAGE_PIXEL_SCALE')
    # Match the helper's integer crop centers; use the actual centered contain
    # square (including half-pixel offsets) for each output pixel's source map.
    left=image[0]+(image[2]-image[0]-side)//2;top=image[1]+(image[3]-image[1]-side)//2
    actual_left=image[0]+(image[2]-image[0]-side)/2;actual_top=image[1]+(image[3]-image[1]-side)/2
    source=decode_png(IMAGE_BYTES,2,2);patches=[];expected=[]
    for i in range(4):
        x=left+(side//4 if i%2==0 else side*3//4)-4;y=top+(side//4 if i<2 else side*3//4)-4
        patches.append([x,y,x+8,y+8]);samples=[]
        for row in range(8):
            for column in range(8):
                u=max(0,min(1,(x+column+.5-actual_left)*2/side-.5))
                v=max(0,min(1,(y+row+.5-actual_top)*2/side-.5))
                samples.append(tuple(round(source[0][c]*(1-u)*(1-v)+source[1][c]*u*(1-v)
                    +source[2][c]*(1-u)*v+source[3][c]*u*v) for c in range(3)))
        expected.append(samples)
    return patches,expected


def validate_packet(packet, image):
    require(type(packet) is bytes and len(MAGIC)+4 <= len(packet) <= len(MAGIC)+4+4*(4+4096)
            and packet.startswith(MAGIC), 'SYNC_IMAGE_PACKET_BOUND')
    offset = len(MAGIC); count, = struct.unpack_from('>I', packet, offset); offset += 4
    require(count == 4, 'SYNC_IMAGE_PACKET_COUNT')
    frames = []
    for _ in range(4):
        require(offset+4 <= len(packet), 'SYNC_IMAGE_PACKET_TRUNCATED')
        size, = struct.unpack_from('>I', packet, offset); offset += 4
        require(8 <= size <= 4096 and offset+size <= len(packet), 'SYNC_IMAGE_PACKET_FRAME_BOUND')
        frames.append(packet[offset:offset+size]); offset += size
    require(offset == len(packet), 'SYNC_IMAGE_PACKET_TRAILING')
    original = decode_png(IMAGE_BYTES,2,2); observations = []
    require(len(set(original)) == 4 and all(p[3] == 255 for p in original), 'SYNC_IMAGE_ORIGINAL_FOUR_OPAQUE_COLORS')
    patches,expected=source_samples(image)
    for index, frame in enumerate(frames):
        pixels = decode_png(frame,8,8)
        # Bounded sRGB rounding/AX edge quantization tolerance, after mapping
        # all 64 samples through the actual contain geometry and bilinear scale.
        maximum = max(abs(pixel[channel]-reference[channel]) for pixel,reference in zip(pixels,expected[index]) for channel in range(3))
        require(all(pixel[3] == 255 for pixel in pixels) and maximum <= 16, 'SYNC_IMAGE_ORIGINAL_PIXEL_MISMATCH')
        observations.append(dict(index=index,bytes=len(frame),sha256=digest(frame),width=8,height=8,
            source_crop=patches[index],source_pixel_sRGB=list(original[index][:3]),maximum_channel_error=maximum,
            allowed_channel_error=16,opaque_pixels=64))
    return frames, dict(four_original_source_pixel_centers_verified=True,source_png_sha256=digest(IMAGE_BYTES),
        packet_sha256=digest(packet),source_dimensions=[2,2],ROI_dimensions=[8,8],image_bounds=image,
        object_fit='contain',sample_model='sRGB bilinear at physical pixel centers',observations=observations,
        source_fixture_decode_alone_is_not_production_presentation=True)
